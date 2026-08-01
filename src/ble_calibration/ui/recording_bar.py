"""Operator controls for manual direction selection and recording."""

from __future__ import annotations

from typing import Optional, Tuple

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..domain import Direction
from ..domain.enums import SessionPhase
from ..session import ManualCaptureSnapshot

PHASE_LABELS = {
    SessionPhase.IDLE: "待选择方向",
    SessionPhase.READY: "可以开始",
    SessionPhase.WAITING_LOCK: "记录中 · 等待实车闭锁",
    SessionPhase.WAITING_UNLOCK: "记录中 · 已闭锁，等待实车解锁",
    SessionPhase.AWAITING_DISTANCES: "已解锁 · 继续记录，等待输入距离和手动结束",
    SessionPhase.READY_TO_FINISH: "已解锁且距离完整 · 继续记录，等待手动结束",
    SessionPhase.COMPLETE: "方向完成",
    SessionPhase.INCOMPLETE: "方向不完整",
}


class RecordingBar(QFrame):
    start_requested = Signal(object, float)
    finish_requested = Signal(object, object)
    redo_requested = Signal(object)
    complete_test_requested = Signal()
    default_speed_edited = Signal(float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("directionCard")
        self._source_ready = True
        self._recording = False
        self._loading_default_speed = False
        root = QVBoxLayout(self)
        root.setContentsMargins(13, 10, 13, 10)
        root.setSpacing(6)
        controls = QHBoxLayout()
        controls.setSpacing(8)

        title = QLabel("手动方向记录")
        title.setObjectName("sectionTitle")
        controls.addWidget(title)
        controls.addWidget(QLabel("方向"))
        self.direction_combo = QComboBox()
        for direction in Direction:
            self.direction_combo.addItem(direction.label, direction)
        controls.addWidget(self.direction_combo)

        controls.addWidget(QLabel("闭锁距离"))
        self.lock_distance = self._distance_spin()
        controls.addWidget(self.lock_distance)
        controls.addWidget(QLabel("解锁距离"))
        self.unlock_distance = self._distance_spin()
        controls.addWidget(self.unlock_distance)
        controls.addWidget(QLabel("默认步速"))
        self.default_walking_speed = QDoubleSpinBox()
        self.default_walking_speed.setRange(0.1, 5.0)
        self.default_walking_speed.setDecimals(1)
        self.default_walking_speed.setSingleStep(0.1)
        self.default_walking_speed.setValue(1.0)
        self.default_walking_speed.setSuffix(" m/s")
        self.default_walking_speed.valueChanged.connect(
            self._default_speed_changed
        )
        controls.addWidget(self.default_walking_speed)
        controls.addWidget(QLabel("本次步速"))
        self.walking_speed = QDoubleSpinBox()
        self.walking_speed.setRange(0.1, 5.0)
        self.walking_speed.setDecimals(1)
        self.walking_speed.setSingleStep(0.1)
        self.walking_speed.setValue(1.0)
        self.walking_speed.setSuffix(" m/s")
        controls.addWidget(self.walking_speed)

        self.start_button = QPushButton("开始记录")
        self.start_button.setObjectName("primaryButton")
        self.start_button.clicked.connect(self._emit_start)
        controls.addWidget(self.start_button)
        self.finish_button = QPushButton("手动结束")
        self.finish_button.setObjectName("dangerButton")
        self.finish_button.setEnabled(False)
        self.finish_button.clicked.connect(self._emit_finish)
        controls.addWidget(self.finish_button)
        controls.addStretch()
        root.addLayout(controls)

        footer = QHBoxLayout()
        self.status_label = QLabel("待选择方向")
        self.status_label.setObjectName("mutedLabel")
        footer.addWidget(self.status_label, 1)
        self.redo_button = QPushButton("重录所选方向")
        self.redo_button.clicked.connect(
            lambda: self.redo_requested.emit(self.selected_direction)
        )
        footer.addWidget(self.redo_button)
        self.redo_button.hide()
        self.complete_test_button = QPushButton("完成本次测试并保存")
        self.complete_test_button.clicked.connect(
            self.complete_test_requested.emit
        )
        footer.addWidget(self.complete_test_button)
        root.addLayout(footer)

    @staticmethod
    def _distance_spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-1.0, 100.0)
        spin.setSpecialValueText("未输入")
        spin.setDecimals(1)
        spin.setSingleStep(0.1)
        spin.setValue(-1.0)
        spin.setSuffix(" m")
        return spin

    @property
    def selected_direction(self) -> Direction:
        return self.direction_combo.currentData()

    def distances(self) -> Tuple[Optional[float], Optional[float]]:
        lock = self.lock_distance.value()
        unlock = self.unlock_distance.value()
        return (
            None if lock < 0 else lock,
            None if unlock < 0 else unlock,
        )

    def _emit_start(self) -> None:
        self.start_requested.emit(
            self.selected_direction,
            self.walking_speed.value(),
        )

    def _emit_finish(self) -> None:
        lock, unlock = self.distances()
        self.finish_requested.emit(lock, unlock)

    def set_recording(self, recording: bool) -> None:
        self._recording = recording
        self.direction_combo.setEnabled(not recording)
        self.walking_speed.setEnabled(not recording)
        self.default_walking_speed.setEnabled(not recording)
        self.start_button.setEnabled(not recording and self._source_ready)
        self.finish_button.setEnabled(recording)
        self.redo_button.setEnabled(not recording)
        self.complete_test_button.setEnabled(not recording)
        self.lock_distance.setEnabled(True)
        self.unlock_distance.setEnabled(True)

    def set_source_ready(self, ready: bool) -> None:
        self._source_ready = ready
        self.start_button.setEnabled(not self._recording and ready)

    def reset_distances(self) -> None:
        self.lock_distance.setValue(-1.0)
        self.unlock_distance.setValue(-1.0)

    def set_default_walking_speed(self, value: float) -> None:
        self._loading_default_speed = True
        self.default_walking_speed.setValue(value)
        if not self._recording:
            self.walking_speed.setValue(value)
        self._loading_default_speed = False

    def reset_walking_speed(self) -> None:
        if not self._recording:
            self.walking_speed.setValue(self.default_walking_speed.value())

    def _default_speed_changed(self, value: float) -> None:
        if self._loading_default_speed:
            return
        if not self._recording:
            self.walking_speed.setValue(value)
        self.default_speed_edited.emit(value)

    def set_snapshot(self, snapshot: ManualCaptureSnapshot) -> None:
        text = PHASE_LABELS[snapshot.phase]
        if snapshot.source_finished and snapshot.dataset is not None:
            text += " · 数据源已结束，请点击“手动结束”"
        if snapshot.error:
            text = f"采集错误：{snapshot.error} · 可手动结束保存已有数据"
        if snapshot.dataset is not None:
            text += f" · {snapshot.dataset.record.sample_count} 点"
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            "color:#ff6b78;" if snapshot.error else "color:#8fc7ff;"
        )

    def set_finished(self, direction: Direction, complete: bool) -> None:
        self.set_recording(False)
        self.reset_walking_speed()
        self.status_label.setText(
            f"{direction.label}已保存为{'完整' if complete else '不完整'}方向"
        )
        self.status_label.setStyleSheet(
            "color:#58d68d;" if complete else "color:#f4c95d;"
        )

    def select_next_unrecorded(self, recorded: Tuple[Direction, ...]) -> None:
        recorded_set = set(recorded)
        for index in range(self.direction_combo.count()):
            direction = self.direction_combo.itemData(index)
            if direction not in recorded_set:
                self.direction_combo.setCurrentIndex(index)
                return
