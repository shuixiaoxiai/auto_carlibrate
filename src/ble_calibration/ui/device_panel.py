"""ZLG CAN device settings and connection controls."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..can.source import SourceState, SourceStatus
from ..config import CanSettings


STATE_LABELS = {
    SourceState.DISCONNECTED: "未连接",
    SourceState.CONNECTING: "正在连接",
    SourceState.CONNECTED: "已连接",
    SourceState.RUNNING: "正在接收",
    SourceState.STOPPING: "正在断开",
    SourceState.STOPPED: "已断开",
    SourceState.ERROR: "连接错误",
}


class DevicePanel(QFrame):
    connect_requested = Signal(object)
    disconnect_requested = Signal()
    settings_saved = Signal(object)
    realtime_save_requested = Signal()
    realtime_stop_requested = Signal()

    def __init__(
        self,
        settings: CanSettings,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("directionCard")
        self._source_attached = False
        self._recording = False
        self._session_recording = False

        root = QVBoxLayout(self)
        root.setContentsMargins(13, 10, 13, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("ZLG CAN 设备")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        self.status_label = QLabel("● 未连接")
        self.status_label.setObjectName("mutedLabel")
        header.addWidget(self.status_label)
        self.frame_count_label = QLabel("接收 0 帧")
        self.frame_count_label.setObjectName("mutedLabel")
        header.addWidget(self.frame_count_label)
        header.addStretch()
        self.save_button = QPushButton("保存配置")
        self.save_button.clicked.connect(self._emit_save)
        header.addWidget(self.save_button)
        self.connect_button = QPushButton("连接设备")
        self.connect_button.setObjectName("primaryButton")
        self.connect_button.clicked.connect(self._toggle_connection)
        header.addWidget(self.connect_button)
        self.realtime_button = QPushButton("实时保存")
        self.realtime_button.setObjectName("primaryButton")
        self.realtime_button.clicked.connect(self._toggle_realtime_saving)
        header.addWidget(self.realtime_button)
        self.realtime_status_label = QLabel("未保存")
        self.realtime_status_label.setObjectName("mutedLabel")
        header.addWidget(self.realtime_status_label)
        root.addLayout(header)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.addWidget(QLabel("接口"), 0, 0)
        self.interface_edit = QLineEdit()
        self.interface_edit.setReadOnly(True)
        grid.addWidget(self.interface_edit, 0, 1)
        grid.addWidget(QLabel("设备类型"), 0, 2)
        self.device_type_edit = QLineEdit()
        grid.addWidget(self.device_type_edit, 0, 3)
        grid.addWidget(QLabel("设备索引"), 0, 4)
        self.device_index_spin = QSpinBox()
        self.device_index_spin.setRange(0, 32)
        grid.addWidget(self.device_index_spin, 0, 5)
        grid.addWidget(QLabel("通道"), 0, 6)
        self.channel_spin = QSpinBox()
        self.channel_spin.setRange(0, 32)
        grid.addWidget(self.channel_spin, 0, 7)

        grid.addWidget(QLabel("仲裁域波特率"), 1, 0)
        self.bitrate_spin = self._bitrate_spin()
        grid.addWidget(self.bitrate_spin, 1, 1)
        grid.addWidget(QLabel("数据域波特率"), 1, 2)
        self.data_bitrate_spin = self._bitrate_spin()
        grid.addWidget(self.data_bitrate_spin, 1, 3)
        self.resistance_check = QCheckBox("启用终端电阻")
        grid.addWidget(self.resistance_check, 1, 4, 1, 2)
        grid.addWidget(QLabel("library 路径"), 1, 6)
        library_layout = QHBoxLayout()
        library_layout.setContentsMargins(0, 0, 0, 0)
        self.library_path_edit = QLineEdit()
        self.library_path_edit.setPlaceholderText(
            "留空使用 zlgcan 包默认路径"
        )
        library_layout.addWidget(self.library_path_edit, 1)
        self.browse_button = QPushButton("浏览")
        self.browse_button.clicked.connect(self._browse_library)
        library_layout.addWidget(self.browse_button)
        grid.addLayout(library_layout, 1, 7)
        grid.setColumnStretch(3, 1)
        grid.setColumnStretch(7, 2)
        root.addLayout(grid)

        self.set_settings(settings)

    @staticmethod
    def _bitrate_spin() -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, 10_000_000)
        spin.setSingleStep(100_000)
        return spin

    def set_settings(self, settings: CanSettings) -> None:
        self.interface_edit.setText(settings.interface)
        self.device_type_edit.setText(settings.device_type)
        self.device_index_spin.setValue(settings.device_index)
        self.channel_spin.setValue(settings.channel)
        self.bitrate_spin.setValue(settings.bitrate)
        self.data_bitrate_spin.setValue(settings.data_bitrate)
        self.resistance_check.setChecked(settings.resistance_enabled)
        self.library_path_edit.setText(settings.library_path or "")

    def settings(self) -> CanSettings:
        return CanSettings(
            interface=self.interface_edit.text().strip(),
            device_type=self.device_type_edit.text().strip(),
            device_index=self.device_index_spin.value(),
            channel=self.channel_spin.value(),
            bitrate=self.bitrate_spin.value(),
            data_bitrate=self.data_bitrate_spin.value(),
            resistance_enabled=self.resistance_check.isChecked(),
            library_path=self.library_path_edit.text().strip() or None,
        )

    def _browse_library(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择 ZLG library 目录",
            self.library_path_edit.text(),
        )
        if selected:
            self.library_path_edit.setText(selected)

    def _emit_save(self) -> None:
        self.settings_saved.emit(self.settings())

    def _toggle_connection(self) -> None:
        if self._source_attached:
            self.disconnect_requested.emit()
        else:
            self.connect_requested.emit(self.settings())

    def set_connection_snapshot(
        self,
        status: Optional[SourceStatus],
        error: Optional[str],
        frame_count: int,
        *,
        source_attached: bool,
    ) -> None:
        self._source_attached = source_attached
        state = SourceState.DISCONNECTED if status is None else status.state
        text = STATE_LABELS[state]
        detail = error or ("" if status is None else status.message)
        if detail:
            text += f" · {detail}"
        self.status_label.setText(f"● {text}")
        is_error = error is not None or state is SourceState.ERROR
        is_ready = state in (SourceState.CONNECTED, SourceState.RUNNING)
        color = "#ff6b78" if is_error else "#58d68d" if is_ready else "#f4c95d"
        self.status_label.setStyleSheet(f"color:{color};")
        self.frame_count_label.setText(f"接收 {frame_count:,} 帧")
        self.connect_button.setText(
            "断开设备" if source_attached else "连接设备"
        )
        self._update_editability()

    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        self._update_editability()

    def set_realtime_snapshot(self, recording: bool, frame_count: int) -> None:
        self._session_recording = recording
        self.realtime_button.setText("结束保存" if recording else "实时保存")
        self.realtime_status_label.setText(
            f"● 实时保存 {frame_count:,} 帧" if recording else "未保存"
        )
        self.realtime_status_label.setStyleSheet(
            "color:#ff6b78;" if recording else ""
        )
        self._update_editability()

    def _toggle_realtime_saving(self) -> None:
        if self._session_recording:
            self.realtime_stop_requested.emit()
        else:
            self.realtime_save_requested.emit()

    def _update_editability(self) -> None:
        editing_enabled = not self._source_attached and not self._recording
        for widget in (
            self.device_type_edit,
            self.device_index_spin,
            self.channel_spin,
            self.bitrate_spin,
            self.data_bitrate_spin,
            self.resistance_check,
            self.library_path_edit,
            self.browse_button,
            self.save_button,
        ):
            widget.setEnabled(editing_enabled)
        self.connect_button.setEnabled(not self._recording and not self._session_recording)
        self.realtime_button.setEnabled(
            self._source_attached and not self._recording
        )
