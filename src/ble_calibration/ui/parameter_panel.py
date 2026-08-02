"""Editable cloud thresholds, strategies and synchronized statistics."""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Mapping, Optional, Sequence, Tuple, Union

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..analysis import GroupedRecomputeResult, RecomputeResult
from ..cloud import CloudDocument, CloudParameters
from ..domain.enums import NODE_ORDER
from .summary_panel import SummaryPanel
from .strategy_status import StrategyActivation, strategy_statuses

STRATEGY_ATTRIBUTES = (
    ("quickLock", "quick_lock"),
    ("quickUnlock", "quick_unlock"),
    ("mstThanSlave", "mst_than_slave"),
    ("bevelAngle", "bevel_angle"),
)


class ParameterPanel(QFrame):
    parameters_edited = Signal()
    decode_requested = Signal(str)
    encode_requested = Signal()
    restore_requested = Signal()
    hide_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("parameterPanel")
        self._loading = False
        self.threshold_spins: Dict[str, Tuple[QSpinBox, ...]] = {}
        self.strategy_spins: Dict[str, Dict[str, QSpinBox]] = {}
        self._strategy_tab_indices: Dict[str, int] = {}
        self._document: Optional[CloudDocument] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(11)

        header = QHBoxLayout()
        title = QLabel("What-if 参数")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        self.mode_label = QLabel("尚未解码")
        self.mode_label.setObjectName("mutedLabel")
        header.addWidget(self.mode_label)
        header.addStretch()
        self.restore_button = QPushButton("一键还原")
        self.restore_button.clicked.connect(self.restore_requested.emit)
        header.addWidget(self.restore_button)
        self.hide_button = QPushButton("隐藏参数")
        self.hide_button.clicked.connect(self.hide_requested.emit)
        header.addWidget(self.hide_button)
        root.addLayout(header)

        self.summary_panel = SummaryPanel()
        root.addWidget(self.summary_panel)

        self.threshold_group = QGroupBox("5 节点阈值（dBm，0 表示禁用）")
        self.threshold_layout = QGridLayout(self.threshold_group)
        self.threshold_layout.addWidget(QLabel(""), 0, 0)
        for column, node in enumerate(NODE_ORDER, start=1):
            self.threshold_layout.addWidget(QLabel(node.label), 0, column)
        self._add_threshold_row("解锁阈值", "unlock", 1)
        self._add_threshold_row("闭锁阈值", "lock", 2)
        root.addWidget(self.threshold_group)

        self.strategy_tabs = QTabWidget()
        self.strategy_tabs.setFixedHeight(205)
        self.strategy_tabs.setToolTip("绿色：已开启；灰色：未开启；橙色：配置无效")
        root.addWidget(self.strategy_tabs)

        cloud_group = QGroupBox("云推 HEX")
        cloud_layout = QVBoxLayout(cloud_group)
        self.hex_edit = QPlainTextEdit()
        self.hex_edit.setPlaceholderText("粘贴云推 HEX 后点击“解码”")
        self.hex_edit.setMaximumHeight(46)
        cloud_layout.addWidget(self.hex_edit)
        cloud_actions = QHBoxLayout()
        self.decode_button = QPushButton("解码")
        self.decode_button.clicked.connect(
            lambda: self.decode_requested.emit(self.hex_edit.toPlainText())
        )
        cloud_actions.addWidget(self.decode_button)
        self.encode_button = QPushButton("编码并复制")
        self.encode_button.clicked.connect(self.encode_requested.emit)
        cloud_actions.addWidget(self.encode_button)
        self.codec_status = QLabel("")
        self.codec_status.setObjectName("mutedLabel")
        cloud_actions.addWidget(self.codec_status)
        cloud_actions.addStretch()
        cloud_layout.addLayout(cloud_actions)
        root.addWidget(cloud_group)

    def _add_threshold_row(self, label: str, key: str, row: int) -> None:
        self.threshold_layout.addWidget(QLabel(label), row, 0)
        spins = []
        for column in range(1, 6):
            spin = QSpinBox()
            spin.setRange(-128, 0)
            spin.setSuffix(" dB")
            spin.valueChanged.connect(self._notify_edit)
            self.threshold_layout.addWidget(spin, row, column)
            spins.append(spin)
        self.threshold_spins[key] = tuple(spins)

    def _clear_strategy_controls(self) -> None:
        while self.strategy_tabs.count():
            widget = self.strategy_tabs.widget(0)
            self.strategy_tabs.removeTab(0)
            widget.deleteLater()
        self.strategy_spins.clear()
        self._strategy_tab_indices.clear()

    def set_document(self, document: CloudDocument) -> None:
        self._loading = True
        self._document = document
        parameters = document.parameters
        self.hex_edit.setPlainText(document.encode_hex())
        for spin, value in zip(
            self.threshold_spins["unlock"],
            parameters.unlock_thresholds,
        ):
            spin.setValue(value)
        for spin, value in zip(
            self.threshold_spins["lock"],
            parameters.lock_thresholds,
        ):
            spin.setValue(value)

        self._clear_strategy_controls()
        if parameters.mst_unlock is not None:
            group = self._node_strategy_group(
                "mstUnlock",
                parameters.mst_unlock,
            )
            self._add_strategy_tab(
                group,
                "主节点单独解锁 mstUnlock",
                "mstUnlock",
                parameters,
            )
        for external_name, attribute in STRATEGY_ATTRIBUTES:
            values = getattr(parameters, attribute)
            if values is not None:
                group = self._mapping_strategy_group(
                    external_name,
                    values,
                )
                self._add_strategy_tab(group, external_name, external_name, parameters)
        self.strategy_tabs.setVisible(self.strategy_tabs.count() > 0)
        self._loading = False

    def _add_strategy_tab(
        self,
        group: QGroupBox,
        title: str,
        key: str,
        parameters: CloudParameters,
    ) -> None:
        status = strategy_statuses(parameters)[key]
        index = self.strategy_tabs.addTab(group, title)
        self._strategy_tab_indices[key] = index
        self.strategy_tabs.setTabIcon(index, self._status_icon(status.activation))
        self.strategy_tabs.setTabToolTip(index, f"{title}：{status.label}；{status.detail}")

    @staticmethod
    def _status_icon(activation: StrategyActivation) -> QIcon:
        colors = {
            StrategyActivation.ENABLED: "#58d68d",
            StrategyActivation.DISABLED: "#7f8c8d",
            StrategyActivation.INVALID: "#f5a623",
        }
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colors[activation]))
        painter.drawEllipse(2, 2, 8, 8)
        painter.end()
        return QIcon(pixmap)

    def _node_strategy_group(
        self,
        key: str,
        values: Sequence[int],
    ) -> QGroupBox:
        group = QGroupBox()
        layout = QGridLayout(group)
        controls: Dict[str, QSpinBox] = {}
        for index, (node, value) in enumerate(zip(NODE_ORDER, values)):
            spin = QSpinBox()
            spin.setRange(-128, 0)
            spin.setValue(value)
            spin.valueChanged.connect(self._notify_edit)
            row = index // 2
            column = (index % 2) * 2
            layout.addWidget(QLabel(node.label), row, column)
            layout.addWidget(spin, row, column + 1)
            controls[node.value] = spin
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        self.strategy_spins[key] = controls
        return group

    def _mapping_strategy_group(
        self,
        key: str,
        values: Mapping[str, int],
    ) -> QGroupBox:
        group = QGroupBox()
        layout = QGridLayout(group)
        controls: Dict[str, QSpinBox] = {}
        for index, (field, value) in enumerate(values.items()):
            spin = QSpinBox()
            spin.setRange(0, 15)
            spin.setValue(value)
            spin.valueChanged.connect(self._notify_edit)
            row = index // 2
            column = (index % 2) * 2
            layout.addWidget(QLabel(field), row, column)
            layout.addWidget(spin, row, column + 1)
            controls[field] = spin
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        self.strategy_spins[key] = controls
        return group

    def _notify_edit(self) -> None:
        if not self._loading:
            self._refresh_strategy_tab_statuses()
            self.parameters_edited.emit()

    def _refresh_strategy_tab_statuses(self) -> None:
        if self._document is None:
            return
        unlock, lock, mst_unlock, strategies = self.values()
        parameters = replace(
            self._document.parameters,
            unlock_thresholds=unlock,
            lock_thresholds=lock,
            mst_unlock=mst_unlock,
            quick_lock=strategies.get("quickLock"),
            quick_unlock=strategies.get("quickUnlock"),
            mst_than_slave=strategies.get("mstThanSlave"),
            bevel_angle=strategies.get("bevelAngle"),
        )
        for key, status in strategy_statuses(parameters).items():
            index = self._strategy_tab_indices.get(key)
            if index is None:
                continue
            self.strategy_tabs.setTabIcon(index, self._status_icon(status.activation))
            self.strategy_tabs.setTabToolTip(
                index,
                f"{self.strategy_tabs.tabText(index)}：{status.label}；{status.detail}",
            )

    def values(
        self,
    ) -> Tuple[
        Tuple[int, ...],
        Tuple[int, ...],
        Optional[Tuple[int, ...]],
        Dict[str, Dict[str, int]],
    ]:
        unlock = tuple(spin.value() for spin in self.threshold_spins["unlock"])
        lock = tuple(spin.value() for spin in self.threshold_spins["lock"])
        mst_controls = self.strategy_spins.get("mstUnlock")
        mst_unlock = (
            None
            if mst_controls is None
            else tuple(mst_controls[node.value].value() for node in NODE_ORDER)
        )
        strategies = {
            strategy: {
                field: spin.value()
                for field, spin in controls.items()
            }
            for strategy, controls in self.strategy_spins.items()
            if strategy != "mstUnlock"
        }
        return unlock, lock, mst_unlock, strategies

    def set_result(
        self,
        result: Union[RecomputeResult, GroupedRecomputeResult],
        *,
        using_original: bool,
        encoded_hex: str,
    ) -> None:
        self.summary_panel.set_result(result)
        self.mode_label.setText(
            (
                "原始参数 · 使用实测动作距离"
                if using_original
                else "What-if 已修改 · 使用当前虚线投影距离"
            )
            + f" · 全组重算 {result.elapsed_ms:.2f} ms"
        )
        if self.hex_edit.toPlainText() != encoded_hex:
            self.hex_edit.setPlainText(encoded_hex)

    def show_codec_status(self, text: str, error: bool = False) -> None:
        self.codec_status.setText(text)
        self.codec_status.setStyleSheet(
            "color: #ff6b78;" if error else "color: #58d68d;"
        )
