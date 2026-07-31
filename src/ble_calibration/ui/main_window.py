"""Main desktop workspace for eight-direction BLE calibration."""

from __future__ import annotations

import time
from typing import Callable, Dict, Optional, Tuple

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QInputDialog,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..cloud import CloudCodecError
from ..can.source import CanSource, SourceState, SourceStatus
from ..config import CanSettings
from ..domain import Direction, DirectionStatus
from ..session import (
    ManualCaptureCoordinator,
    ManualCaptureSnapshot,
    SessionStateError,
)
from .device_panel import DevicePanel
from .direction_chart import DirectionChartCard
from .parameter_panel import ParameterPanel
from .project_dialog import ProjectPickerDialog
from .project_workspace import ProjectWorkspace
from .recording_bar import RecordingBar
from .state import CalibrationUiState
from .theme import APP_STYLESHEET


class CalibrationMainWindow(QMainWindow):
    def __init__(
        self,
        state: CalibrationUiState,
        project_name: str = "未命名项目",
        manual_capture: Optional[ManualCaptureCoordinator] = None,
        source_factory: Optional[Callable[[Direction], CanSource]] = None,
        live_source_factory: Optional[Callable[[CanSettings], CanSource]] = None,
        can_settings: Optional[CanSettings] = None,
        settings_saver: Optional[Callable[[CanSettings], None]] = None,
        workspace: Optional[ProjectWorkspace] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.state = state
        self.project_name = project_name
        self.manual_capture = manual_capture
        self.source_factory = source_factory
        self.live_source_factory = live_source_factory
        self.can_settings = can_settings or CanSettings()
        self.settings_saver = settings_saver
        self.workspace = workspace
        self._dirty = workspace is not None and not workspace.persisted
        self._automated_exit = False
        source_count = sum(
            factory is not None
            for factory in (source_factory, live_source_factory)
        )
        if source_count > 1:
            raise ValueError("only one manual CAN source mode can be active")
        if (manual_capture is None) != (source_count == 0):
            raise ValueError(
                "manual_capture and one source factory must be provided together"
            )
        self.cards: Dict[Direction, DirectionChartCard] = {}
        self.last_what_if_refresh_ms: Optional[float] = None
        self._pending_measurement: Optional[
            Tuple[Direction, Optional[float], Optional[float], float]
        ] = None

        self.setWindowTitle(f"BLE Calibration · {project_name}")
        self.setMinimumSize(1100, 720)
        self.resize(1600, 1000)
        self.setStyleSheet(APP_STYLESHEET)
        self._build_toolbar()
        self._build_central()
        self.setStatusBar(QStatusBar())

        self.parameter_timer = QTimer(self)
        self.parameter_timer.setSingleShot(True)
        self.parameter_timer.setInterval(55)
        self.parameter_timer.timeout.connect(self._apply_parameter_edits)
        self.measurement_timer = QTimer(self)
        self.measurement_timer.setSingleShot(True)
        self.measurement_timer.setInterval(80)
        self.measurement_timer.timeout.connect(self._apply_measurement_edit)
        self.capture_timer = QTimer(self)
        self.capture_timer.setInterval(100)
        self.capture_timer.timeout.connect(self._poll_manual_capture)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(30_000)
        self.autosave_timer.timeout.connect(self._save_recovery_snapshot)
        if self.workspace is not None:
            self.autosave_timer.start()
        self._last_preview_sample_count = -1
        if self.manual_capture is not None:
            self.capture_timer.start()
            if self.live_source_factory is not None:
                self.recording_bar.set_source_ready(False)
                self.data_status.setText("● ZLG 设备未连接")
                assert self.device_panel is not None
                self.device_panel.set_connection_snapshot(
                    None,
                    None,
                    0,
                    source_attached=False,
                )
            else:
                self.recording_bar.set_source_ready(True)
                self.data_status.setText("● Mock 手动采集待机")

        self.parameter_panel.set_document(self.state.current_document)
        for direction, card in self.cards.items():
            card.set_dataset(self.state.dataset_for(direction))
        if self.live_source_factory is not None:
            initial_message = "ZLG 实车采集工作区已就绪"
        elif self.manual_capture is not None:
            initial_message = "Mock 手动采集工作区已就绪"
        else:
            initial_message = "Mock/回放数据已载入"
        self._refresh_result(initial_message)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        brand = QLabel("  BLE CALIBRATION  ")
        brand.setStyleSheet(
            "color:#70b7ff; font-size:15px; font-weight:800; letter-spacing:1px;"
        )
        toolbar.addWidget(brand)
        toolbar.addSeparator()
        self.project_label = QLabel(self.project_name)
        self.project_label.setStyleSheet(
            "font-size:14px; font-weight:600; padding:0 8px;"
        )
        toolbar.addWidget(self.project_label)
        self.new_project_action = QAction("新建", self)
        self.new_project_action.triggered.connect(self._new_project)
        toolbar.addAction(self.new_project_action)
        self.open_project_action = QAction("打开", self)
        self.open_project_action.triggered.connect(self._open_project)
        toolbar.addAction(self.open_project_action)
        self.save_project_action = QAction("保存", self)
        self.save_project_action.triggered.connect(self._save_project)
        toolbar.addAction(self.save_project_action)
        for action in (
            self.new_project_action,
            self.open_project_action,
            self.save_project_action,
        ):
            action.setEnabled(self.workspace is not None)
        spacer = QWidget()
        spacer.setSizePolicy(
            spacer.sizePolicy().Policy.Expanding,
            spacer.sizePolicy().Policy.Preferred,
        )
        toolbar.addWidget(spacer)
        self.data_status = QLabel("● 离线数据")
        self.data_status.setStyleSheet("color:#58d68d; padding:0 10px;")
        toolbar.addWidget(self.data_status)
        self.toggle_parameters_action = QAction("隐藏参数", self)
        self.toggle_parameters_action.triggered.connect(self._toggle_parameters)
        toolbar.addAction(self.toggle_parameters_action)

    def _build_central(self) -> None:
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(
            self.scroll.horizontalScrollBarPolicy().ScrollBarAlwaysOff
        )
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 24)
        layout.setSpacing(13)

        self.parameter_panel = ParameterPanel()
        self.parameter_panel.parameters_edited.connect(
            lambda: self.parameter_timer.start()
        )
        self.parameter_panel.decode_requested.connect(self._decode_cloud)
        self.parameter_panel.encode_requested.connect(self._encode_cloud)
        self.parameter_panel.restore_requested.connect(self._restore_parameters)
        self.parameter_panel.hide_requested.connect(
            lambda: self.set_parameters_visible(False)
        )
        layout.addWidget(self.parameter_panel)

        self.show_parameters_top = QPushButton("显示当前 What-if 参数")
        self.show_parameters_top.setObjectName("primaryButton")
        self.show_parameters_top.clicked.connect(
            lambda: self.set_parameters_visible(True)
        )
        self.show_parameters_top.hide()
        layout.addWidget(self.show_parameters_top)

        self.device_panel: Optional[DevicePanel] = None
        if self.live_source_factory is not None:
            self.device_panel = DevicePanel(self.can_settings)
            self.device_panel.connect_requested.connect(
                self._connect_live_device
            )
            self.device_panel.disconnect_requested.connect(
                self._disconnect_live_device
            )
            self.device_panel.settings_saved.connect(
                self._save_can_settings
            )
            layout.addWidget(self.device_panel)

        self.recording_bar = RecordingBar()
        self.recording_bar.setVisible(self.manual_capture is not None)
        self.recording_bar.start_requested.connect(self._start_manual_recording)
        self.recording_bar.finish_requested.connect(self._finish_manual_recording)
        self.recording_bar.redo_requested.connect(self._redo_direction)
        self.recording_bar.complete_test_requested.connect(self._complete_test)
        layout.addWidget(self.recording_bar)

        guide = QFrame()
        guide_layout = QHBoxLayout(guide)
        guide_layout.setContentsMargins(4, 0, 4, 0)
        guide_layout.addWidget(
            QLabel(
                "实线＝条件开始满足（文字/触发节点/瞬时 RSSI 均取此时刻）　"
                "虚线＝动作时刻　背景：绿色优 / 黄色良 / 红色差"
            )
        )
        guide_layout.addStretch()
        layout.addWidget(guide)

        for direction in Direction:
            card = DirectionChartCard(direction)
            card.measurements_edited.connect(self._queue_measurement_edit)
            self.cards[direction] = card
            layout.addWidget(card)

        self.show_parameters_bottom = QPushButton("显示当前 What-if 参数并返回顶部")
        self.show_parameters_bottom.setObjectName("primaryButton")
        self.show_parameters_bottom.clicked.connect(
            lambda: self.set_parameters_visible(True)
        )
        self.show_parameters_bottom.hide()
        layout.addWidget(self.show_parameters_bottom)
        layout.addStretch()

        self.scroll.setWidget(container)
        self.setCentralWidget(self.scroll)

    def _toggle_parameters(self) -> None:
        self.set_parameters_visible(not self.parameter_panel.isVisible())

    def set_parameters_visible(self, visible: bool) -> None:
        self.parameter_panel.setVisible(visible)
        self.show_parameters_top.setVisible(not visible)
        self.show_parameters_bottom.setVisible(not visible)
        self.toggle_parameters_action.setText(
            "隐藏参数" if visible else "显示参数"
        )
        if visible:
            self.scroll.verticalScrollBar().setValue(0)

    def _save_can_settings(self, settings: CanSettings) -> bool:
        try:
            if self.settings_saver is not None:
                self.settings_saver(settings)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "配置保存失败", str(error))
            return False
        self.can_settings = settings
        if self.workspace is not None:
            self.workspace.capture_channel = settings.channel
        self.statusBar().showMessage("ZLG CAN 配置已保存", 4000)
        return True

    def _connect_live_device(self, settings: CanSettings) -> None:
        if self.live_source_factory is None or self.manual_capture is None:
            return
        if self.manual_capture.is_active:
            QMessageBox.information(self, "正在记录", "请先结束当前方向。")
            return
        if not self._save_can_settings(settings):
            return
        try:
            self.manual_capture.connect(self.live_source_factory(settings))
        except (OSError, ValueError, RuntimeError, SessionStateError) as error:
            assert self.device_panel is not None
            self.device_panel.set_connection_snapshot(
                SourceStatus(SourceState.ERROR, str(error)),
                str(error),
                0,
                source_attached=False,
            )
            self.data_status.setText("● ZLG 连接失败")
            return
        assert self.device_panel is not None
        self.device_panel.set_connection_snapshot(
            SourceStatus(SourceState.CONNECTING, "正在打开设备"),
            None,
            0,
            source_attached=True,
        )
        self.recording_bar.set_source_ready(False)
        self.data_status.setText("● ZLG 正在连接")

    def _disconnect_live_device(self) -> None:
        if self.manual_capture is None or self.device_panel is None:
            return
        try:
            self.manual_capture.disconnect()
        except (RuntimeError, SessionStateError) as error:
            QMessageBox.information(self, "无法断开设备", str(error))
            return
        self.device_panel.set_connection_snapshot(
            None,
            None,
            0,
            source_attached=False,
        )
        self.recording_bar.set_source_ready(False)
        recorded = len(self.state.datasets)
        self.data_status.setText(
            f"● ZLG 未连接 · 已记录 {recorded}/8"
        )

    def _set_live_data_status(
        self,
        snapshot: ManualCaptureSnapshot,
    ) -> None:
        if snapshot.error:
            self.data_status.setText("● ZLG 采集错误")
            self.data_status.setStyleSheet("color:#ff6b78; padding:0 10px;")
            return
        status = snapshot.source_status
        state = SourceState.DISCONNECTED if status is None else status.state
        if self.manual_capture is not None and self.manual_capture.is_active:
            direction = snapshot.direction
            label = "" if direction is None else f" {direction.label}"
            self.data_status.setText(f"● ZLG 正在记录{label}")
            self.data_status.setStyleSheet("color:#58d68d; padding:0 10px;")
        elif state in (SourceState.CONNECTED, SourceState.RUNNING):
            self.data_status.setText(
                f"● ZLG 已连接 · 接收 {snapshot.frame_count:,} 帧"
            )
            self.data_status.setStyleSheet("color:#58d68d; padding:0 10px;")
        elif state is SourceState.CONNECTING:
            self.data_status.setText("● ZLG 正在连接")
            self.data_status.setStyleSheet("color:#f4c95d; padding:0 10px;")
        else:
            self.data_status.setText(
                f"● ZLG 未连接 · 已记录 {len(self.state.datasets)}/8"
            )
            self.data_status.setStyleSheet("color:#f4c95d; padding:0 10px;")

    def _decode_cloud(self, hex_text: str) -> None:
        try:
            self.state.replace_cloud_hex(hex_text)
        except (CloudCodecError, ValueError) as error:
            self.parameter_panel.show_codec_status(f"解码失败：{error}", True)
            return
        self.parameter_panel.set_document(self.state.current_document)
        self.parameter_panel.show_codec_status("解码成功，已建立还原点")
        self._refresh_result("云推参数已解码并重算")
        self._mark_dirty()

    def _encode_cloud(self) -> None:
        encoded = self.state.encoded_hex()
        QApplication.clipboard().setText(encoded)
        self.parameter_panel.hex_edit.setPlainText(encoded)
        self.parameter_panel.show_codec_status(
            f"已编码并复制 · {len(encoded) // 2} 字节"
        )

    def _restore_parameters(self) -> None:
        self.state.restore()
        self.parameter_panel.set_document(self.state.current_document)
        self.parameter_panel.show_codec_status("已还原到本次解码参数")
        self._refresh_result("参数、8 方向图和统计已还原")
        self._mark_dirty()

    def _apply_parameter_edits(self) -> None:
        started = time.perf_counter()
        unlock, lock, mst_unlock, strategies = self.parameter_panel.values()
        try:
            self.state.apply_updates(
                unlock_thresholds=unlock,
                lock_thresholds=lock,
                mst_unlock=mst_unlock,
                strategy_updates=strategies,
            )
        except (CloudCodecError, ValueError) as error:
            self.parameter_panel.show_codec_status(f"参数无效：{error}", True)
            return
        self._refresh_result("阈值/策略、图表及优良差汇总已同步重算")
        self.last_what_if_refresh_ms = (time.perf_counter() - started) * 1000.0
        self.parameter_panel.show_codec_status(
            f"What-if 已重算 · 全界面 {self.last_what_if_refresh_ms:.1f} ms"
        )
        self.statusBar().showMessage(
            "阈值/策略、8 方向图及优良差汇总已同步重算 · "
            f"全界面 {self.last_what_if_refresh_ms:.1f} ms",
            5000,
        )
        self._mark_dirty()

    def _queue_measurement_edit(
        self,
        direction: Direction,
        lock_distance: Optional[float],
        unlock_distance: Optional[float],
        walking_speed: float,
    ) -> None:
        self._pending_measurement = (
            direction,
            lock_distance,
            unlock_distance,
            walking_speed,
        )
        self.measurement_timer.start()

    def _apply_measurement_edit(self) -> None:
        if self._pending_measurement is None:
            return
        direction, lock_distance, unlock_distance, walking_speed = (
            self._pending_measurement
        )
        self._pending_measurement = None
        self.state.update_measurements(
            direction,
            lock_distance,
            unlock_distance,
            walking_speed,
        )
        card = self.cards[direction]
        card.set_dataset(self.state.dataset_for(direction))
        self._refresh_result(f"{direction.label}距离/步速已更新")
        self._mark_dirty()

    def _start_manual_recording(
        self,
        direction: Direction,
        walking_speed: float,
    ) -> None:
        if self.manual_capture is None:
            return
        existing = self.state.dataset_for(direction)
        if existing is not None:
            choice = QMessageBox.question(
                self,
                "重录方向",
                f"{direction.label}已有记录，开始后将以新记录替换。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
            self.state.remove_direction(direction)
            self.cards[direction].set_dataset(None)
            self._refresh_result(f"{direction.label}旧记录已移除，等待重录")
            self._mark_dirty()
        recorder = None
        try:
            if (
                self.live_source_factory is not None
                and not self.manual_capture.is_connected
            ):
                raise SessionStateError("请先连接 ZLG CAN 设备")
            raw_data_file = None
            if self.workspace is not None:
                recorder, raw_data_file = self.workspace.capture_target(direction)
            if self.source_factory is not None:
                self.manual_capture.begin(
                    direction,
                    self.source_factory(direction),
                    walking_speed_mps=walking_speed,
                    raw_data_file=raw_data_file,
                    recorder=recorder,
                )
            else:
                self.manual_capture.begin_connected(
                    direction,
                    walking_speed_mps=walking_speed,
                    raw_data_file=raw_data_file,
                    recorder=recorder,
                )
        except (OSError, ValueError, RuntimeError, SessionStateError) as error:
            if recorder is not None:
                recorder.stop()
            self.recording_bar.status_label.setText(f"无法开始：{error}")
            self.recording_bar.status_label.setStyleSheet("color:#ff6b78;")
            return
        self._last_preview_sample_count = -1
        self.recording_bar.reset_distances()
        self.recording_bar.set_recording(True)
        if self.device_panel is not None:
            self.device_panel.set_recording(True)
        self.data_status.setText(f"● 正在记录 {direction.label}")
        self.statusBar().showMessage(
            f"{direction.label}开始记录：远离等待闭锁，随后靠近等待解锁",
            5000,
        )

    def _redo_direction(self, direction: Direction) -> None:
        if self.manual_capture is None or self.manual_capture.is_active:
            return
        if self.state.dataset_for(direction) is None:
            self.recording_bar.status_label.setText(
                f"{direction.label}尚无记录，可直接点击“开始记录”"
            )
            return
        choice = QMessageBox.question(
            self,
            "重录方向",
            f"确定删除{direction.label}当前记录并重新采集？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self.state.remove_direction(direction)
        self.cards[direction].set_dataset(None)
        self._refresh_result(f"{direction.label}已清除，等待重录")
        self.recording_bar.reset_distances()
        self.recording_bar.status_label.setText(
            f"{direction.label}旧记录已清除，请点击“开始记录”"
        )
        self._mark_dirty()
        self._save_recovery_snapshot()

    def _complete_test(self) -> None:
        if self.manual_capture is not None and self.manual_capture.is_active:
            QMessageBox.information(self, "正在记录", "请先手动结束当前方向。")
            return
        saved = self._save_project() if self.workspace is not None else True
        if not saved:
            return
        complete = sum(
            dataset.record.status is DirectionStatus.COMPLETE
            for dataset in self.state.datasets
        )
        incomplete = len(self.state.datasets) - complete
        self.recording_bar.status_label.setText(
            f"本次测试已保存：完整 {complete}，不完整 {incomplete}"
        )
        self.recording_bar.status_label.setStyleSheet("color:#58d68d;")

    def _poll_manual_capture(self) -> None:
        if self.manual_capture is None:
            return
        snapshot = self.manual_capture.snapshot()
        if self.device_panel is not None:
            source_attached = self.manual_capture.uses_persistent_source
            self.device_panel.set_connection_snapshot(
                snapshot.source_status,
                snapshot.error,
                snapshot.frame_count,
                source_attached=source_attached,
            )
            self.device_panel.set_recording(self.manual_capture.is_active)
            self.recording_bar.set_source_ready(
                self.manual_capture.is_connected
            )
            self._set_live_data_status(snapshot)
        self.recording_bar.set_snapshot(snapshot)
        preview = snapshot.dataset
        if (
            preview is None
            or preview.record.sample_count == self._last_preview_sample_count
        ):
            return
        self._last_preview_sample_count = preview.record.sample_count
        card = self.cards[preview.record.direction]
        card.set_dataset(preview)
        card.set_enabled_for_data(False)
        preview_result = self.state.service.recompute(
            self.state.current_document.parameters,
            (preview,),
        )
        card.set_analysis(
            preview_result.directions.get(preview.record.direction),
            self.state.current_document.parameters,
        )

    def _finish_manual_recording(
        self,
        lock_distance: Optional[float],
        unlock_distance: Optional[float],
    ) -> None:
        if self.manual_capture is None:
            return
        try:
            dataset = self.manual_capture.finish(
                lock_distance_m=lock_distance,
                unlock_distance_m=unlock_distance,
            )
        except (ValueError, RuntimeError, SessionStateError) as error:
            self.recording_bar.status_label.setText(f"无法结束：{error}")
            self.recording_bar.status_label.setStyleSheet("color:#ff6b78;")
            return
        self.state.upsert_dataset(dataset)
        card = self.cards[dataset.record.direction]
        card.set_dataset(dataset)
        card.set_enabled_for_data(True)
        self._refresh_result(f"{dataset.record.direction.label}已手动结束并保存")
        complete = dataset.record.status is DirectionStatus.COMPLETE
        self.recording_bar.set_finished(dataset.record.direction, complete)
        if self.device_panel is not None:
            self.device_panel.set_recording(False)
        recorded = tuple(item.record.direction for item in self.state.datasets)
        self.recording_bar.select_next_unrecorded(recorded)
        if self.live_source_factory is None:
            self.data_status.setText(f"● 已记录 {len(recorded)}/8")
        else:
            self.data_status.setText(
                f"● ZLG 已连接 · 已记录 {len(recorded)}/8"
            )
        self._mark_dirty()
        self._save_recovery_snapshot()

    def _mark_dirty(self) -> None:
        if self.workspace is None:
            return
        self._dirty = True
        self.setWindowTitle(
            f"BLE Calibration · {self.workspace.name} *"
        )

    def _save_project(self) -> bool:
        if self.workspace is None:
            return False
        try:
            self.workspace.save(self.state)
        except (OSError, ValueError, RuntimeError) as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return False
        self._dirty = False
        self.project_name = self.workspace.name
        self.project_label.setText(self.workspace.name)
        self.setWindowTitle(f"BLE Calibration · {self.workspace.name}")
        self.statusBar().showMessage(
            f"项目已保存：{self.workspace.database_path}",
            5000,
        )
        return True

    def _save_recovery_snapshot(self) -> None:
        if self.workspace is None or not self._dirty:
            return
        try:
            self.workspace.save_recovery(self.state)
        except (OSError, ValueError, RuntimeError) as error:
            self.statusBar().showMessage(f"自动恢复快照失败：{error}", 5000)

    def _confirm_pending_changes(self) -> bool:
        if not self._dirty or self.workspace is None:
            return True
        choice = QMessageBox.warning(
            self,
            "项目尚未保存",
            "当前项目有未保存更改。",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self._save_project()
        return True

    def _new_project(self) -> None:
        if self.workspace is None:
            return
        if self.manual_capture is not None and self.manual_capture.is_active:
            QMessageBox.information(self, "正在记录", "请先手动结束当前方向。")
            return
        if not self._confirm_pending_changes():
            return
        name, accepted = QInputDialog.getText(
            self,
            "新建项目",
            "项目名称",
            text="新建八方向标定",
        )
        if not accepted or not name.strip():
            return
        workspace = ProjectWorkspace.create(
            self.workspace.database_path,
            name.strip(),
            capture_format=self.workspace.capture_format,
            capture_channel=self.workspace.capture_channel,
        )
        state = CalibrationUiState(self.state.original_document, ())
        self._replace_workspace(workspace, state)
        self._mark_dirty()

    def _open_project(self) -> None:
        if self.workspace is None:
            return
        if self.manual_capture is not None and self.manual_capture.is_active:
            QMessageBox.information(self, "正在记录", "请先手动结束当前方向。")
            return
        if not self._confirm_pending_changes():
            return
        project_id = ProjectPickerDialog.pick(
            self.workspace.database_path,
            self,
        )
        if project_id is None:
            return
        try:
            workspace, state = ProjectWorkspace.load(
                self.workspace.database_path,
                project_id,
            )
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            QMessageBox.critical(self, "打开失败", str(error))
            return
        self._replace_workspace(workspace, state)
        self.statusBar().showMessage(f"已打开项目：{workspace.name}", 5000)

    def _replace_workspace(
        self,
        workspace: ProjectWorkspace,
        state: CalibrationUiState,
    ) -> None:
        if self.manual_capture is not None:
            self.manual_capture.close()
            self.manual_capture = ManualCaptureCoordinator()
        self.workspace = workspace
        self.state = state
        self.project_name = workspace.name
        self.project_label.setText(workspace.name)
        self.parameter_panel.set_document(state.current_document)
        for direction, card in self.cards.items():
            card.set_dataset(state.dataset_for(direction))
        self._dirty = not workspace.persisted
        self.setWindowTitle(f"BLE Calibration · {workspace.name}")
        self._refresh_result("项目工作区已切换")
        recorded = tuple(item.record.direction for item in state.datasets)
        self.recording_bar.select_next_unrecorded(recorded)
        if self.live_source_factory is not None:
            self.recording_bar.set_source_ready(False)
            assert self.device_panel is not None
            self.device_panel.set_connection_snapshot(
                None,
                None,
                0,
                source_attached=False,
            )
            self.data_status.setText(
                f"● ZLG 未连接 · 已记录 {len(recorded)}/8"
            )
        else:
            self.recording_bar.set_source_ready(True)
            self.data_status.setText(f"● 已记录 {len(recorded)}/8")

    def _refresh_result(self, message: str) -> None:
        result = self.state.result
        self.parameter_panel.set_result(
            result,
            using_original=self.state.using_original,
            encoded_hex=self.state.encoded_hex(),
        )
        parameters = self.state.current_document.parameters
        for direction, card in self.cards.items():
            card.set_analysis(result.directions.get(direction), parameters)
        self.statusBar().showMessage(
            f"{message} · 核心重算 {result.elapsed_ms:.2f} ms",
            5000,
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._automated_exit:
            if self.manual_capture is not None:
                self.manual_capture.close()
            super().closeEvent(event)
            return
        if self.manual_capture is not None and self.manual_capture.is_active:
            choice = QMessageBox.warning(
                self,
                "当前方向仍在记录",
                "关闭前将手动结束并保存当前已有数据，是否继续？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            lock_distance, unlock_distance = self.recording_bar.distances()
            self._finish_manual_recording(lock_distance, unlock_distance)
        if not self._confirm_pending_changes():
            event.ignore()
            return
        if self.manual_capture is not None:
            self.manual_capture.close()
        super().closeEvent(event)

    def close_for_automation(self) -> None:
        self._automated_exit = True
        self.close()
