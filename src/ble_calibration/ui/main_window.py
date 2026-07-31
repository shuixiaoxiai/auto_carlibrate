"""Main desktop workspace for eight-direction BLE calibration."""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..cloud import CloudCodecError
from ..domain import Direction
from .direction_chart import DirectionChartCard
from .parameter_panel import ParameterPanel
from .state import CalibrationUiState
from .theme import APP_STYLESHEET


class CalibrationMainWindow(QMainWindow):
    def __init__(
        self,
        state: CalibrationUiState,
        project_name: str = "未命名项目",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.state = state
        self.project_name = project_name
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

        self.parameter_panel.set_document(self.state.current_document)
        for direction, card in self.cards.items():
            card.set_dataset(self.state.dataset_for(direction))
        self._refresh_result("Mock/回放数据已载入")

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
        project = QLabel(self.project_name)
        project.setStyleSheet("font-size:14px; font-weight:600; padding:0 8px;")
        toolbar.addWidget(project)
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

    def _decode_cloud(self, hex_text: str) -> None:
        try:
            self.state.replace_cloud_hex(hex_text)
        except (CloudCodecError, ValueError) as error:
            self.parameter_panel.show_codec_status(f"解码失败：{error}", True)
            return
        self.parameter_panel.set_document(self.state.current_document)
        self.parameter_panel.show_codec_status("解码成功，已建立还原点")
        self._refresh_result("云推参数已解码并重算")

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
