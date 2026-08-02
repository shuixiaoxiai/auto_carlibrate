"""Main desktop workspace for eight-direction BLE calibration."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Tuple
from uuid import uuid4

from PySide6.QtCore import Qt, QThread, QTimer, Slot
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
from ..can.recording import RotatingBlfRecorder
from ..can.source import CanSource, SourceState, SourceStatus
from ..config import CanSettings
from ..domain import Direction, DirectionStatus
from ..optimization import (
    OptimizationConfig,
    OptimizationResult,
    optimization_readiness,
    strategy_updates,
)
from ..session import (
    ManualCaptureCoordinator,
    ManualCaptureSnapshot,
    SessionStateError,
)
from .device_panel import DevicePanel
from .direction_chart import MEAN_VIEW, DirectionChartCard
from .optimization_dialog import OptimizationDialog, OptimizationWorker
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
        self._optimization_dialog: Optional[OptimizationDialog] = None
        self._optimization_thread: Optional[QThread] = None
        self._optimization_worker: Optional[OptimizationWorker] = None
        self._optimization_snapshot = None
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
            Tuple[Direction, int, Optional[float], Optional[float], float]
        ] = None
        self._active_capture_group: Optional[int] = None
        self._active_recording_id: Optional[str] = None
        self._realtime_recorder: Optional[RotatingBlfRecorder] = None
        self._realtime_manifest_path = None
        self._realtime_started_at: Optional[str] = None

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
        self._last_preview_render_monotonic = 0.0
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
        self.recording_bar.set_default_walking_speed(
            self.state.default_walking_speed_mps
        )
        self._sync_all_cards(reset_selection=True)
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
        self.parameter_panel.optimization_requested.connect(
            self._show_automatic_optimization
        )
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
            self.device_panel.realtime_save_requested.connect(
                self._start_realtime_saving
            )
            self.device_panel.realtime_stop_requested.connect(
                self._stop_realtime_saving
            )
            layout.addWidget(self.device_panel)

        self.recording_bar = RecordingBar()
        self.recording_bar.setVisible(self.manual_capture is not None)
        self.recording_bar.start_requested.connect(self._start_manual_recording)
        self.recording_bar.finish_requested.connect(self._finish_manual_recording)
        self.recording_bar.redo_requested.connect(self._redo_direction)
        self.recording_bar.complete_test_requested.connect(self._complete_test)
        self.recording_bar.default_speed_edited.connect(
            self._update_default_walking_speed
        )
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
            card.view_changed.connect(self._change_chart_view)
            card.delete_requested.connect(self._delete_group)
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
        recorded = self.state.record_count
        self.data_status.setText(
            f"● ZLG 未连接 · 已记录 {recorded}/24"
        )

    def _start_realtime_saving(self) -> None:
        if self.manual_capture is None or self.workspace is None:
            return
        recorder = None
        try:
            recorder, manifest_path = self.workspace.realtime_capture_target()
            self.manual_capture.start_session_recording(recorder)
        except (OSError, ValueError, RuntimeError, SessionStateError) as error:
            if recorder is not None:
                recorder.stop()
            QMessageBox.warning(self, "无法开始实时保存", str(error))
            return
        self._realtime_recorder = recorder
        self._realtime_manifest_path = manifest_path
        self._realtime_started_at = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        self.statusBar().showMessage("已开始实时保存：将连续记录全部 CAN 帧", 5000)

    def _stop_realtime_saving(self) -> None:
        if (
            self.manual_capture is None
            or self.workspace is None
            or self._realtime_recorder is None
            or self._realtime_manifest_path is None
            or self._realtime_started_at is None
        ):
            return
        try:
            self.manual_capture.stop_session_recording()
            capture_path = self.workspace.finalize_realtime_capture(
                self._realtime_recorder,
                self._realtime_manifest_path,
                started_at=self._realtime_started_at,
            )
        except (OSError, ValueError, RuntimeError, SessionStateError) as error:
            QMessageBox.warning(self, "无法结束实时保存", str(error))
            return
        self._realtime_recorder = None
        self._realtime_manifest_path = None
        self._realtime_started_at = None
        self._mark_dirty()
        self._save_project()
        self.statusBar().showMessage(f"实时保存已结束：{capture_path}", 7000)

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
                f"● ZLG 未连接 · 已记录 {self.state.record_count}/24"
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

    def _show_automatic_optimization(self) -> None:
        if self._optimization_dialog is not None:
            self._optimization_dialog.show()
            self._optimization_dialog.raise_()
            self._optimization_dialog.activateWindow()
            return
        config = OptimizationConfig()
        readiness = optimization_readiness(
            self.state.current_document.parameters,
            self.state.datasets,
            config,
        )
        dialog = OptimizationDialog(
            readiness,
            self.state.result,
            config,
            self,
        )
        dialog.start_requested.connect(self._start_automatic_optimization)
        dialog.cancel_requested.connect(self._cancel_automatic_optimization)
        dialog.apply_requested.connect(self._apply_automatic_recommendation)
        dialog.finished.connect(self._optimization_dialog_closed)
        self._optimization_dialog = dialog
        dialog.show()

    def _start_automatic_optimization(self, allow_strategy_fallback: bool) -> None:
        if self._optimization_thread is not None:
            return
        dialog = self._optimization_dialog
        if dialog is None:
            return
        config = OptimizationConfig(
            allow_strategy_fallback=allow_strategy_fallback,
        )
        self._optimization_snapshot = self._optimization_input_signature()
        thread = QThread(self)
        worker = OptimizationWorker(
            self.state.current_document.parameters,
            tuple(self.state.datasets),
            config,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(
            dialog.update_progress,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.finished.connect(
            self._automatic_optimization_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.failed.connect(
            self._automatic_optimization_failed,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.cancelled.connect(
            self._automatic_optimization_cancelled,
            Qt.ConnectionType.QueuedConnection,
        )
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(
            self._automatic_optimization_thread_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        thread.finished.connect(thread.deleteLater)
        self._optimization_thread = thread
        self._optimization_worker = worker
        dialog.begin_progress()
        self._refresh_optimization_availability()
        thread.start()

    def _cancel_automatic_optimization(self) -> None:
        if self._optimization_worker is not None:
            self._optimization_worker.request_cancel()

    @Slot(object)
    def _automatic_optimization_finished(
        self,
        result: OptimizationResult,
    ) -> None:
        if self._optimization_dialog is not None:
            self._optimization_dialog.show_result(result)
        self.statusBar().showMessage(
            f"自动优化完成 · 评估 {result.evaluated_candidates} 组候选 · "
            f"耗时 {result.elapsed_ms / 1000.0:.1f}s",
            7000,
        )

    @Slot(str)
    def _automatic_optimization_failed(self, message: str) -> None:
        if self._optimization_dialog is not None:
            self._optimization_dialog.show_error(message)
        self.statusBar().showMessage(f"自动优化失败：{message}", 7000)

    @Slot()
    def _automatic_optimization_cancelled(self) -> None:
        if self._optimization_dialog is not None:
            self._optimization_dialog.show_cancelled()
        self.statusBar().showMessage("自动优化已取消，参数未发生变化", 5000)

    @Slot()
    def _automatic_optimization_thread_finished(self) -> None:
        self._optimization_thread = None
        self._optimization_worker = None
        if (
            self._optimization_dialog is not None
            and not self._optimization_dialog.isVisible()
        ):
            self._optimization_dialog = None
        self._refresh_optimization_availability()

    def _apply_automatic_recommendation(
        self,
        result: OptimizationResult,
    ) -> None:
        if not result.can_apply:
            return
        if self._optimization_snapshot != self._optimization_input_signature():
            QMessageBox.warning(
                self,
                "优化结果已过期",
                "优化期间参数或方向数据发生了变化，请重新运行自动优化。",
            )
            return
        parameters = result.recommendation.parameters
        try:
            self.state.apply_updates(
                unlock_thresholds=parameters.unlock_thresholds,
                lock_thresholds=parameters.lock_thresholds,
                mst_unlock=parameters.mst_unlock,
                strategy_updates=strategy_updates(parameters),
            )
        except (CloudCodecError, ValueError) as error:
            QMessageBox.critical(self, "无法应用自动推荐", str(error))
            return
        self.parameter_panel.set_document(self.state.current_document)
        self.parameter_panel.show_codec_status(
            "自动推荐已应用到 What-if；尚未写车，请检查后编码复制"
        )
        self._refresh_result("自动推荐已应用到 What-if，8 方向结果已同步重算")
        self._mark_dirty()
        if self._optimization_dialog is not None:
            self._optimization_dialog.accept()

    def _optimization_dialog_closed(self, _result: int) -> None:
        if self._optimization_thread is not None:
            self._cancel_automatic_optimization()
            return
        self._optimization_dialog = None

    def _optimization_input_signature(self):
        return (
            self.state.current_document.encode_hex(),
            tuple(
                (
                    dataset.record.recording_id,
                    dataset.record.direction.value,
                    dataset.record.group_index,
                    len(dataset.samples),
                    dataset.record.actual_lock_distance_m,
                    dataset.record.actual_unlock_distance_m,
                    dataset.record.walking_speed_mps,
                    tuple(
                        (event.event_type.value, event.timestamp)
                        for event in dataset.record.vehicle_events
                    ),
                )
                for dataset in self.state.datasets
            ),
        )

    def _refresh_optimization_availability(self) -> None:
        running = self._optimization_thread is not None
        readiness = optimization_readiness(
            self.state.current_document.parameters,
            self.state.datasets,
            OptimizationConfig(),
        )
        if running:
            detail = "自动优化正在运行"
        elif readiness.errors:
            detail = "；".join(readiness.errors)
        else:
            detail = "使用全部完整原始方向组自动搜索安全参数"
        self.parameter_panel.set_optimization_available(
            readiness.can_start and not running,
            detail,
        )

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
        group_index: int,
        lock_distance: Optional[float],
        unlock_distance: Optional[float],
        walking_speed: float,
    ) -> None:
        self._pending_measurement = (
            direction,
            group_index,
            lock_distance,
            unlock_distance,
            walking_speed,
        )
        self.measurement_timer.start()

    def _apply_measurement_edit(self) -> None:
        if self._pending_measurement is None:
            return
        direction, group_index, lock_distance, unlock_distance, walking_speed = (
            self._pending_measurement
        )
        self._pending_measurement = None
        self.state.update_measurements(
            direction,
            lock_distance,
            unlock_distance,
            walking_speed,
            group_index,
        )
        self._refresh_result(
            f"{direction.label}第 {group_index} 组距离/步速已更新",
            changed_direction=direction,
        )
        self._mark_dirty()

    def _update_default_walking_speed(self, walking_speed: float) -> None:
        try:
            self.state.update_default_walking_speed(walking_speed)
        except ValueError as error:
            self.statusBar().showMessage(f"默认步速无效：{error}", 5000)
            return
        self._mark_dirty()
        self.statusBar().showMessage(
            f"项目默认步速已更新为 {walking_speed:.1f} m/s，仅影响后续采集",
            5000,
        )

    def _start_manual_recording(
        self,
        direction: Direction,
        walking_speed: float,
    ) -> None:
        if self.manual_capture is None:
            return
        group_index = self.state.next_capture_group(direction)
        recording_id = str(uuid4())
        replacing = self.state.dataset_for(direction, group_index) is not None
        recorder = None
        try:
            if (
                self.live_source_factory is not None
                and not self.manual_capture.is_connected
            ):
                raise SessionStateError("请先连接 ZLG CAN 设备")
            raw_data_file = None
            if self.workspace is not None:
                recorder, raw_data_file = self.workspace.capture_target(
                    direction,
                    group_index,
                    recording_id,
                )
            if self.source_factory is not None:
                self.manual_capture.begin(
                    direction,
                    self.source_factory(direction),
                    walking_speed_mps=walking_speed,
                    raw_data_file=raw_data_file,
                    recorder=recorder,
                    group_index=group_index,
                    recording_id=recording_id,
                )
            else:
                self.manual_capture.begin_connected(
                    direction,
                    walking_speed_mps=walking_speed,
                    raw_data_file=raw_data_file,
                    recorder=recorder,
                    group_index=group_index,
                    recording_id=recording_id,
                )
        except (OSError, ValueError, RuntimeError, SessionStateError) as error:
            if recorder is not None:
                recorder.stop()
            self.recording_bar.status_label.setText(f"无法开始：{error}")
            self.recording_bar.status_label.setStyleSheet("color:#ff6b78;")
            return
        self._active_capture_group = group_index
        self._active_recording_id = recording_id
        self.cards[direction].set_selected_view(group_index)
        self._last_preview_sample_count = -1
        self._last_preview_render_monotonic = 0.0
        self.recording_bar.reset_distances()
        self.recording_bar.set_recording(True)
        if self.device_panel is not None:
            self.device_panel.set_recording(True)
        action = "覆盖" if replacing else "新增"
        self.data_status.setText(
            f"● 正在记录 {direction.label}第 {group_index} 组"
        )
        self.statusBar().showMessage(
            f"{direction.label}开始{action}第 {group_index} 组："
            "远离等待闭锁，随后靠近等待解锁",
            5000,
        )

    def _redo_direction(self, direction: Direction) -> None:
        if self.manual_capture is None or self.manual_capture.is_active:
            return
        group_index = self.state.latest_group_for(direction)
        if group_index is None:
            self.recording_bar.status_label.setText(
                f"{direction.label}尚无记录，可直接点击“开始记录”"
            )
            return
        self._delete_group(direction, group_index)

    def _change_chart_view(self, direction: Direction, view_index: int) -> None:
        self._render_card(direction)
        view_name = "均值" if view_index == MEAN_VIEW else f"第 {view_index} 组"
        self.statusBar().showMessage(
            f"{direction.label}已切换到{view_name}",
            2500,
        )

    def _delete_group(self, direction: Direction, group_index: int) -> None:
        if self.manual_capture is not None and self.manual_capture.is_active:
            QMessageBox.information(self, "正在记录", "请先结束当前方向。")
            return
        if self.state.dataset_for(direction, group_index) is None:
            return
        choice = QMessageBox.question(
            self,
            "删除组数据",
            f"确定删除{direction.label}第 {group_index} 组数据？\n"
            "原始采集文件会保留，但本组将从项目和统计中移除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        self.state.remove_dataset(direction, group_index)
        latest_group = self.state.latest_group_for(direction)
        self.cards[direction].set_group_availability(
            self.state.group_indices(direction),
            preferred_view=latest_group,
        )
        self._refresh_result(
            f"{direction.label}第 {group_index} 组已删除",
            changed_direction=direction,
        )
        self.recording_bar.status_label.setText(
            f"{direction.label}第 {group_index} 组已删除；下次采集优先补空位"
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
            self.device_panel.set_realtime_snapshot(
                snapshot.session_recording,
                snapshot.session_frame_count,
            )
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
        now = time.monotonic()
        if (
            not snapshot.source_finished
            and now - self._last_preview_render_monotonic < 0.5
        ):
            return
        self._last_preview_render_monotonic = now
        self._last_preview_sample_count = preview.record.sample_count
        card = self.cards[preview.record.direction]
        card.set_selected_view(preview.record.group_index)
        card.set_dataset(preview, editable=False)
        preview_result = self.state.service.single_service.recompute(
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
        card.set_group_availability(
            self.state.group_indices(dataset.record.direction),
            preferred_view=dataset.record.group_index,
        )
        self._refresh_result(
            f"{dataset.record.direction.label}第 {dataset.record.group_index} 组"
            "已手动结束并保存",
            changed_direction=dataset.record.direction,
        )
        self._active_capture_group = None
        self._active_recording_id = None
        complete = dataset.record.status is DirectionStatus.COMPLETE
        self.recording_bar.set_finished(dataset.record.direction, complete)
        if self.device_panel is not None:
            self.device_panel.set_recording(False)
        recorded = self.state.recorded_directions
        self.recording_bar.select_next_unrecorded(recorded)
        if self.live_source_factory is None:
            self.data_status.setText(
                f"● 已记录 {self.state.record_count}/24 · "
                f"方向 {len(recorded)}/8"
            )
        else:
            self.data_status.setText(
                f"● ZLG 已连接 · 已记录 {self.state.record_count}/24 · "
                f"方向 {len(recorded)}/8"
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
        if self.manual_capture is not None and self.manual_capture.is_session_recording:
            QMessageBox.information(self, "正在实时保存", "请先点击“结束保存”。")
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
        if self.manual_capture is not None and self.manual_capture.is_session_recording:
            QMessageBox.information(self, "正在实时保存", "请先点击“结束保存”。")
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
        self.recording_bar.set_default_walking_speed(
            state.default_walking_speed_mps
        )
        self._sync_all_cards(reset_selection=True)
        self._dirty = not workspace.persisted
        self.setWindowTitle(f"BLE Calibration · {workspace.name}")
        self._refresh_result("项目工作区已切换")
        recorded = state.recorded_directions
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
                f"● ZLG 未连接 · 已记录 {state.record_count}/24 · "
                f"方向 {len(recorded)}/8"
            )
        else:
            self.recording_bar.set_source_ready(True)
            self.data_status.setText(
                f"● 已记录 {state.record_count}/24 · 方向 {len(recorded)}/8"
            )

    def _refresh_result(
        self,
        message: str,
        *,
        changed_direction: Optional[Direction] = None,
    ) -> None:
        result = self.state.result
        self.parameter_panel.set_result(
            result,
            using_original=self.state.using_original,
            encoded_hex=self.state.encoded_hex(),
        )
        self._refresh_optimization_availability()
        if changed_direction is None:
            for direction in self.cards:
                self._refresh_card_analysis(direction)
        else:
            card = self.cards[changed_direction]
            preferred = (
                self.state.latest_group_for(changed_direction)
                if card.selected_view is None
                else None
            )
            card.set_group_availability(
                self.state.group_indices(changed_direction),
                preferred_view=preferred,
            )
            self._render_card(changed_direction)
        self.statusBar().showMessage(
            f"{message} · 核心重算 {result.elapsed_ms:.2f} ms",
            5000,
        )

    def _sync_all_cards(self, *, reset_selection: bool = False) -> None:
        for direction, card in self.cards.items():
            if reset_selection:
                card.set_selected_view(None)
            preferred = (
                self.state.latest_group_for(direction)
                if card.selected_view is None
                else None
            )
            card.set_group_availability(
                self.state.group_indices(direction),
                preferred_view=preferred,
            )
            self._render_card(direction)

    def _render_card(self, direction: Direction) -> None:
        card = self.cards[direction]
        parameters = self.state.current_document.parameters
        if card.selected_view == MEAN_VIEW:
            mean = self.state.mean_for(direction)
            if mean is None:
                card.set_dataset(None, editable=False)
                card.set_analysis(None, parameters)
                return
            card.set_dataset(
                mean.dataset,
                editable=False,
                mean_context=(
                    mean.group_count,
                    mean.lock_result_count,
                    mean.unlock_result_count,
                ),
            )
            card.set_analysis(mean.analysis, parameters)
            return
        group_index = card.selected_view
        dataset = (
            None
            if group_index is None
            else self.state.dataset_for(direction, group_index)
        )
        card.set_dataset(dataset, editable=True)
        analysis = (
            None
            if group_index is None
            else self.state.result.analysis_for(direction, group_index)
        )
        card.set_analysis(analysis, parameters)

    def _refresh_card_analysis(self, direction: Direction) -> None:
        card = self.cards[direction]
        parameters = self.state.current_document.parameters
        if card.selected_view == MEAN_VIEW:
            mean = self.state.mean_for(direction)
            if mean is None:
                card.set_analysis(None, parameters)
                return
            card.set_mean_context(
                (
                    mean.group_count,
                    mean.lock_result_count,
                    mean.unlock_result_count,
                )
            )
            card.set_analysis(mean.analysis, parameters)
            return
        group_index = card.selected_view
        analysis = (
            None
            if group_index is None
            else self.state.result.analysis_for(direction, group_index)
        )
        card.set_analysis(analysis, parameters)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._automated_exit:
            if self.manual_capture is not None:
                self.manual_capture.close()
            super().closeEvent(event)
            return
        if self._optimization_thread is not None:
            QMessageBox.information(
                self,
                "正在自动优化",
                "请先在自动优化窗口点击“取消优化”，等待任务安全停止。",
            )
            event.ignore()
            return
        if (
            self.manual_capture is not None
            and self.manual_capture.is_session_recording
        ):
            QMessageBox.information(self, "正在实时保存", "请先点击“结束保存”。")
            event.ignore()
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
