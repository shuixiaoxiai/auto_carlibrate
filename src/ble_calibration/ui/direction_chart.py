"""One wide direction card with RSSI curves, distance zones and event details."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..analysis import DirectionDataset
from ..cloud import CloudParameters
from ..domain import (
    Direction,
    DirectionAnalysisResult,
    DirectionStatus,
    EventType,
    StrategyEventResult,
)
from ..domain.enums import NODE_ORDER, StrategyKind
from .theme import GRADE_BRUSHES, NODE_COLORS

STRATEGY_LABELS = {
    StrategyKind.BASE: "基础",
    StrategyKind.MASTER_UNLOCK: "主节点单独解锁",
    StrategyKind.QUICK_LOCK: "快速闭锁",
    StrategyKind.QUICK_UNLOCK: "快速解锁",
    StrategyKind.MASTER_THAN_SLAVE: "主强从弱",
    StrategyKind.BEVEL_ANGLE: "斜角补偿",
}

MEAN_VIEW = 0
GROUP_LABELS = {1: "第一组", 2: "第二组", 3: "第三组"}


def _display_time(timestamp: float, origin: float) -> str:
    return f"{timestamp - origin:.2f}s"


def _grade_text(result: Optional[StrategyEventResult]) -> str:
    if result is None:
        return "未触发"
    distance = "--" if result.distance_m is None else f"{result.distance_m:.1f}m"
    return f"{distance} · {result.grade.label}"


class EventDetail(QWidget):
    def __init__(
        self,
        event_type: EventType,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.event_type = event_type
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.title = QLabel(f"{event_type.label}锁：未触发")
        self.title.setStyleSheet("font-weight: 700; color: #dceaff;")
        layout.addWidget(self.title)
        self.table = QTableWidget(3, 5)
        self.table.setHorizontalHeaderLabels([node.label for node in NODE_ORDER])
        self.table.setVerticalHeaderLabels(["瞬时 RSSI", "距阈值", "0.5s 变化率"])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column in range(5):
            self.table.horizontalHeader().setSectionResizeMode(
                column,
                self.table.horizontalHeader().ResizeMode.Stretch,
            )
        self.table.verticalHeader().setDefaultSectionSize(25)
        self.table.setFixedHeight(126)
        layout.addWidget(self.table)
        self.clear()

    def clear(self) -> None:
        self.title.setText(f"{self.event_type.label}锁：未触发")
        for row in range(3):
            for column in range(5):
                self.table.setItem(row, column, QTableWidgetItem("--"))

    def set_result(
        self,
        result: Optional[StrategyEventResult],
        dataset: DirectionDataset,
        thresholds: Sequence[int],
        origin: float,
    ) -> None:
        if result is None:
            self.clear()
            return
        condition = result.condition
        strategy = STRATEGY_LABELS.get(condition.strategy, condition.strategy.value)
        self.title.setText(
            f"{condition.label} · {strategy} · "
            f"实线 {_display_time(condition.timestamp, origin)} · "
            f"虚线 {_display_time(result.action.timestamp, origin)} · "
            f"{_grade_text(result)}"
        )
        rates = self._rates_at(dataset, condition.timestamp)
        for index, value in enumerate(condition.rssi):
            threshold = thresholds[index]
            delta = None if value is None or threshold == 0 else value - threshold
            cells = (
                "--" if value is None else f"{value} dBm",
                "--" if delta is None else f"{delta:+d} dB",
                "--" if rates[index] is None else f"{rates[index]:+.1f} dB/s",
            )
            for row, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, index, item)

    @staticmethod
    def _rates_at(
        dataset: DirectionDataset,
        condition_timestamp: float,
    ) -> Tuple[Optional[float], ...]:
        before = [
            sample
            for sample in dataset.samples
            if sample.source_timestamp <= condition_timestamp + 1e-9
        ]
        if not before:
            return (None, None, None, None, None)
        current = before[-1]
        target = condition_timestamp - 0.5
        previous = min(
            before,
            key=lambda sample: abs(sample.source_timestamp - target),
        )
        elapsed = current.source_timestamp - previous.source_timestamp
        if elapsed <= 1e-9:
            return (None, None, None, None, None)
        rates: List[Optional[float]] = []
        for current_value, previous_value in zip(current.values, previous.values):
            rates.append(
                None
                if current_value is None or previous_value is None
                else (current_value - previous_value) / elapsed
            )
        return tuple(rates)


class DirectionChartCard(QFrame):
    measurements_edited = Signal(object, int, object, object, float)
    view_changed = Signal(object, int)
    delete_requested = Signal(object, int)

    def __init__(
        self,
        direction: Direction,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.direction = direction
        self.dataset: Optional[DirectionDataset] = None
        self.selected_view: Optional[int] = None
        self._editable = False
        self._mean_context: Optional[Tuple[int, int, int]] = None
        self._origin = 0.0
        self._loading_measurements = False
        self._curves: Dict[object, pg.PlotDataItem] = {}
        self._event_items: List[object] = []
        self._zone_items: List[object] = []
        self.setObjectName("directionCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        root = QVBoxLayout(self)
        root.setContentsMargins(13, 12, 13, 13)
        root.setSpacing(8)

        header = QHBoxLayout()
        self.direction_label = QLabel(direction.label)
        self.direction_label.setObjectName("sectionTitle")
        header.addWidget(self.direction_label)
        self.status_label = QLabel("未开始")
        self.status_label.setObjectName("mutedLabel")
        header.addWidget(self.status_label)
        self.result_label = QLabel("")
        self.result_label.setStyleSheet("color: #9dc7ef;")
        header.addWidget(self.result_label)
        header.addStretch()
        self.group_button_group = QButtonGroup(self)
        self.group_button_group.setExclusive(True)
        self.group_buttons: Dict[int, QPushButton] = {}
        for group_index, label in GROUP_LABELS.items():
            button = QPushButton(label)
            button.setCheckable(True)
            button.setEnabled(False)
            button.clicked.connect(
                lambda checked, index=group_index: self._select_view(index, checked)
            )
            self.group_button_group.addButton(button, group_index)
            self.group_buttons[group_index] = button
            header.addWidget(button)
        self.mean_button = QPushButton("均值")
        self.mean_button.setCheckable(True)
        self.mean_button.setEnabled(False)
        self.mean_button.clicked.connect(
            lambda checked: self._select_view(MEAN_VIEW, checked)
        )
        self.group_button_group.addButton(self.mean_button, MEAN_VIEW)
        header.addWidget(self.mean_button)
        self.delete_button = QPushButton("删除当前组")
        self.delete_button.setObjectName("dangerButton")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._emit_delete)
        header.addWidget(self.delete_button)
        header.addWidget(QLabel("闭锁实测"))
        self.lock_distance = self._distance_spin()
        self.lock_distance.valueChanged.connect(self._emit_measurements)
        header.addWidget(self.lock_distance)
        header.addWidget(QLabel("解锁实测"))
        self.unlock_distance = self._distance_spin()
        self.unlock_distance.valueChanged.connect(self._emit_measurements)
        header.addWidget(self.unlock_distance)
        header.addWidget(QLabel("步速"))
        self.walking_speed = QDoubleSpinBox()
        self.walking_speed.setRange(0.1, 5.0)
        self.walking_speed.setSingleStep(0.1)
        self.walking_speed.setDecimals(1)
        self.walking_speed.setSuffix(" m/s")
        self.walking_speed.valueChanged.connect(self._emit_measurements)
        header.addWidget(self.walking_speed)
        root.addLayout(header)

        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(430)
        self.plot.setBackground("#0a1525")
        self.plot.setLabel("left", "RSSI", units="dBm")
        self.plot.setLabel("bottom", "相对时间", units="s")
        self.plot.setYRange(-105, -35, padding=0)
        self.plot.showGrid(x=True, y=True, alpha=0.18)
        self.plot.getAxis("bottom").setTickSpacing(2, 1)
        self.plot.getPlotItem().setClipToView(True)
        self.plot.getPlotItem().setDownsampling(auto=True, mode="peak")
        self.plot.addLegend(offset=(10, 8), brush=pg.mkBrush(8, 17, 31, 190))
        root.addWidget(self.plot)

        details = QHBoxLayout()
        self.lock_detail = EventDetail(EventType.LOCK)
        self.unlock_detail = EventDetail(EventType.UNLOCK)
        details.addWidget(self.lock_detail, 1)
        details.addWidget(self.unlock_detail, 1)
        root.addLayout(details)

        self.set_enabled_for_data(False)

    @staticmethod
    def _distance_spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-1.0, 100.0)
        spin.setSpecialValueText("未输入")
        spin.setDecimals(1)
        spin.setSingleStep(0.1)
        spin.setSuffix(" m")
        return spin

    def set_enabled_for_data(self, enabled: bool) -> None:
        editable = enabled and self._editable
        self.lock_distance.setEnabled(editable)
        self.unlock_distance.setEnabled(editable)
        self.walking_speed.setEnabled(editable)

    def set_group_availability(
        self,
        available_groups: Sequence[int],
        *,
        preferred_view: Optional[int] = None,
    ) -> None:
        available = set(available_groups)
        for group_index, button in self.group_buttons.items():
            button.setEnabled(group_index in available)
        self.mean_button.setEnabled(bool(available))
        if preferred_view is not None and (
            preferred_view == MEAN_VIEW or preferred_view in available
        ):
            self.set_selected_view(preferred_view)
        elif self.selected_view not in available and self.selected_view != MEAN_VIEW:
            self.set_selected_view(max(available) if available else None)
        elif self.selected_view == MEAN_VIEW and not available:
            self.set_selected_view(None)
        self.delete_button.setEnabled(
            self.selected_view in available and self.selected_view != MEAN_VIEW
        )

    def set_selected_view(self, view_index: Optional[int]) -> None:
        self.selected_view = view_index
        for group_index, button in self.group_buttons.items():
            button.setChecked(view_index == group_index)
        self.mean_button.setChecked(view_index == MEAN_VIEW)
        self.delete_button.setEnabled(
            view_index is not None
            and view_index != MEAN_VIEW
            and self.group_buttons[view_index].isEnabled()
        )

    def _select_view(self, view_index: int, checked: bool) -> None:
        if not checked:
            return
        self.set_selected_view(view_index)
        self.view_changed.emit(self.direction, view_index)

    def _emit_delete(self) -> None:
        if self.selected_view is None or self.selected_view == MEAN_VIEW:
            return
        self.delete_requested.emit(self.direction, self.selected_view)

    def set_dataset(
        self,
        dataset: Optional[DirectionDataset],
        *,
        editable: bool = True,
        mean_context: Optional[Tuple[int, int, int]] = None,
    ) -> None:
        self.dataset = dataset
        self._editable = editable
        self._mean_context = mean_context
        self._clear_curves()
        if dataset is None:
            self.status_label.setText("未记录")
            self.set_enabled_for_data(False)
            return
        self.set_enabled_for_data(True)
        record = dataset.record
        self._origin = record.start_timestamp or 0.0
        self._loading_measurements = True
        self.lock_distance.setValue(
            -1.0
            if record.actual_lock_distance_m is None
            else record.actual_lock_distance_m
        )
        self.unlock_distance.setValue(
            -1.0
            if record.actual_unlock_distance_m is None
            else record.actual_unlock_distance_m
        )
        self.walking_speed.setValue(record.walking_speed_mps)
        self._loading_measurements = False
        status = {
            DirectionStatus.COMPLETE: "已完成",
            DirectionStatus.INCOMPLETE: "不完整",
            DirectionStatus.RECORDING: "记录中",
            DirectionStatus.NOT_STARTED: "未开始",
        }[record.status]
        if mean_context is None:
            group_label = GROUP_LABELS.get(record.group_index, "")
            self.status_label.setText(
                f"{group_label} · {status} · {len(dataset.samples)} 点"
            )
        else:
            group_count, _, _ = mean_context
            self.status_label.setText(
                f"均值 · {group_count} 组 · {len(dataset.samples)} 点"
            )
        if not dataset.samples:
            self.result_label.setText("")
            self.lock_detail.clear()
            self.unlock_detail.clear()
            return
        if record.start_timestamp is None:
            self._origin = dataset.samples[0].source_timestamp

        x_values = [
            sample.source_timestamp - self._origin for sample in dataset.samples
        ]
        for node, color in zip(NODE_ORDER, NODE_COLORS):
            y_values = [sample.value(node) for sample in dataset.samples]
            self._curves[node] = self.plot.plot(
                x_values,
                y_values,
                name=node.label,
                pen=pg.mkPen(color, width=1.8),
                connect="finite",
            )
        duration = max(x_values[-1], 2.0)
        self.plot.setXRange(0, duration, padding=0.01)
        self._render_distance_zones()

    def set_mean_context(self, context: Tuple[int, int, int]) -> None:
        self._mean_context = context
        group_count, _, _ = context
        if self.dataset is not None:
            self.status_label.setText(
                f"均值 · {group_count} 组 · {len(self.dataset.samples)} 点"
            )

    def _clear_curves(self) -> None:
        for curve in self._curves.values():
            self.plot.removeItem(curve)
        self._curves.clear()
        self._clear_items(self._event_items)
        self._clear_items(self._zone_items)

    def _clear_items(self, items: List[object]) -> None:
        for item in items:
            self.plot.removeItem(item)
        items.clear()

    def _emit_measurements(self) -> None:
        if (
            self._loading_measurements
            or self.dataset is None
            or not self._editable
            or self.selected_view is None
            or self.selected_view == MEAN_VIEW
        ):
            return
        lock_value = self.lock_distance.value()
        unlock_value = self.unlock_distance.value()
        self.measurements_edited.emit(
            self.direction,
            self.selected_view,
            None if lock_value < 0 else lock_value,
            None if unlock_value < 0 else unlock_value,
            self.walking_speed.value(),
        )

    def set_analysis(
        self,
        analysis: Optional[DirectionAnalysisResult],
        parameters: CloudParameters,
    ) -> None:
        self._clear_items(self._event_items)
        if self.dataset is None or analysis is None:
            self.lock_detail.clear()
            self.unlock_detail.clear()
            self.result_label.setText("")
            return
        self._add_event_lines(analysis.lock)
        self._add_event_lines(analysis.unlock)
        self.lock_detail.set_result(
            analysis.lock,
            self.dataset,
            parameters.lock_thresholds,
            self._origin,
        )
        self.unlock_detail.set_result(
            analysis.unlock,
            self.dataset,
            parameters.unlock_thresholds,
            self._origin,
        )
        self.result_label.setText(
            f"闭 {_grade_text(analysis.lock)}　解 {_grade_text(analysis.unlock)}"
        )
        if self._mean_context is not None:
            group_count, lock_count, unlock_count = self._mean_context
            self.result_label.setText(
                self.result_label.text()
                + f"　参与：闭 {lock_count}/{group_count} · 解 {unlock_count}/{group_count}"
            )

    def _add_event_lines(self, result: Optional[StrategyEventResult]) -> None:
        if result is None:
            return
        color = "#ffb454" if result.action.event_type is EventType.LOCK else "#57c7ff"
        condition_line = pg.InfiniteLine(
            pos=result.condition.timestamp - self._origin,
            angle=90,
            movable=False,
            pen=pg.mkPen(color, width=2.4, style=Qt.PenStyle.SolidLine),
            label=result.condition.label,
            labelOpts={
                "position": 0.93,
                "color": color,
                "fill": pg.mkBrush(8, 17, 31, 210),
            },
        )
        action_line = pg.InfiniteLine(
            pos=result.action.timestamp - self._origin,
            angle=90,
            movable=False,
            pen=pg.mkPen(color, width=2.0, style=Qt.PenStyle.DashLine),
        )
        self.plot.addItem(condition_line)
        self.plot.addItem(action_line)
        self._event_items.extend((condition_line, action_line))

    def _render_distance_zones(self) -> None:
        self._clear_items(self._zone_items)
        dataset = self.dataset
        if dataset is None or not dataset.samples:
            return
        record = dataset.record
        duration = dataset.samples[-1].source_timestamp - self._origin
        turnaround = self._turnaround_relative()
        lock_event = record.event(EventType.LOCK)
        unlock_event = record.event(EventType.UNLOCK)
        if lock_event is not None and record.actual_lock_distance_m is not None:
            boundaries = [
                0.0,
                lock_event.timestamp
                - self._origin
                + (5.0 - record.actual_lock_distance_m) / record.walking_speed_mps,
                lock_event.timestamp
                - self._origin
                + (8.0 - record.actual_lock_distance_m) / record.walking_speed_mps,
                lock_event.timestamp
                - self._origin
                + (12.0 - record.actual_lock_distance_m) / record.walking_speed_mps,
                lock_event.timestamp
                - self._origin
                + (16.0 - record.actual_lock_distance_m) / record.walking_speed_mps,
                turnaround,
            ]
            self._add_zone_sequence(
                boundaries,
                ("poor", "good", "excellent", "good", "poor"),
                0.0,
                turnaround,
            )
        if unlock_event is not None and record.actual_unlock_distance_m is not None:
            anchor = unlock_event.timestamp - self._origin
            boundaries = [
                turnaround,
                anchor + (record.actual_unlock_distance_m - 8.0)
                / record.walking_speed_mps,
                anchor + (record.actual_unlock_distance_m - 5.0)
                / record.walking_speed_mps,
                anchor + (record.actual_unlock_distance_m - 2.0)
                / record.walking_speed_mps,
                anchor + (record.actual_unlock_distance_m - 0.5)
                / record.walking_speed_mps,
                duration,
            ]
            self._add_zone_sequence(
                boundaries,
                ("poor", "good", "excellent", "good", "poor"),
                turnaround,
                duration,
            )

    def _turnaround_relative(self) -> float:
        assert self.dataset is not None
        samples = self.dataset.samples
        record = self.dataset.record
        lock_event = record.event(EventType.LOCK)
        unlock_event = record.event(EventType.UNLOCK)
        lower = (
            samples[0].source_timestamp
            if lock_event is None
            else lock_event.timestamp
        )
        upper = (
            samples[-1].source_timestamp
            if unlock_event is None
            else unlock_event.timestamp
        )
        candidates = [
            sample
            for sample in samples
            if lower <= sample.source_timestamp <= upper
            and any(value is not None for value in sample.values)
        ]
        if not candidates:
            return (samples[-1].source_timestamp - self._origin) / 2.0
        farthest = min(
            candidates,
            key=lambda sample: sum(
                value for value in sample.values if value is not None
            )
            / sum(value is not None for value in sample.values),
        )
        return farthest.source_timestamp - self._origin

    def _add_zone_sequence(
        self,
        boundaries: Sequence[float],
        grades: Sequence[str],
        minimum: float,
        maximum: float,
    ) -> None:
        clipped = [min(max(value, minimum), maximum) for value in boundaries]
        clipped.sort()
        for left, right, grade in zip(clipped, clipped[1:], grades):
            if right - left <= 1e-6:
                continue
            region = pg.LinearRegionItem(
                values=(left, right),
                orientation="vertical",
                movable=False,
                brush=pg.mkBrush(*GRADE_BRUSHES[grade]),
                pen=pg.mkPen(None),
            )
            region.setZValue(-20)
            self.plot.addItem(region)
            self._zone_items.append(region)
