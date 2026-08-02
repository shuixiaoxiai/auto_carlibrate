"""Automatic threshold optimization workflow dialog and background worker."""

from __future__ import annotations

from threading import Event
from typing import Optional, Sequence

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..analysis import DirectionDataset, GroupedRecomputeResult
from ..cloud.models import CloudParameters
from ..domain.enums import NODE_ORDER, StrategyKind
from ..optimization import (
    AutomaticThresholdOptimizer,
    OptimizationCancelled,
    OptimizationConfig,
    OptimizationProgress,
    OptimizationReadiness,
    OptimizationResult,
)


STRATEGY_LABELS = {
    StrategyKind.MASTER_UNLOCK: "主节点单独解锁",
    StrategyKind.QUICK_LOCK: "快速闭锁",
    StrategyKind.QUICK_UNLOCK: "快速解锁",
    StrategyKind.MASTER_THAN_SLAVE: "主节点强于从节点",
    StrategyKind.BEVEL_ANGLE: "斜角",
}


class OptimizationWorker(QObject):
    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        parameters: CloudParameters,
        datasets: Sequence[DirectionDataset],
        config: OptimizationConfig,
    ) -> None:
        super().__init__()
        self.parameters = parameters
        self.datasets = tuple(datasets)
        self.config = config
        self._cancelled = Event()
        self._last_progress_evaluations = -1
        self._last_progress_phase = ""

    def request_cancel(self) -> None:
        self._cancelled.set()

    @Slot()
    def run(self) -> None:
        try:
            result = AutomaticThresholdOptimizer(self.config).optimize(
                self.parameters,
                self.datasets,
                progress=self._report_progress,
                cancel=self._cancelled.is_set,
            )
        except OptimizationCancelled:
            self.cancelled.emit()
        except Exception as error:  # user-facing worker boundary
            self.failed.emit(f"{type(error).__name__}: {error}")
        else:
            self.finished.emit(result)

    def _report_progress(self, progress: OptimizationProgress) -> None:
        """Limit GUI wakeups while retaining meaningful phase progress."""
        phase_changed = progress.phase != self._last_progress_phase
        enough_new_work = (
            progress.evaluated_candidates - self._last_progress_evaluations >= 20
        )
        if not phase_changed and not enough_new_work:
            return
        self._last_progress_phase = progress.phase
        self._last_progress_evaluations = progress.evaluated_candidates
        self.progress.emit(progress)


class OptimizationDialog(QDialog):
    start_requested = Signal(bool)
    cancel_requested = Signal()
    apply_requested = Signal(object)

    def __init__(
        self,
        readiness: OptimizationReadiness,
        current_result: GroupedRecomputeResult,
        config: OptimizationConfig,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.readiness = readiness
        self.config = config
        self._running = False
        self._result: Optional[OptimizationResult] = None
        self.setWindowTitle("自动阈值优化")
        self.setMinimumSize(980, 720)
        self.resize(1120, 800)

        root = QVBoxLayout(self)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)

        self.precheck_page = self._build_precheck_page(current_result)
        self.progress_page = self._build_progress_page()
        self.result_page = self._build_result_page()
        self.stack.addWidget(self.precheck_page)
        self.stack.addWidget(self.progress_page)
        self.stack.addWidget(self.result_page)

    def _build_precheck_page(self, current_result) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        lock_rate = current_result.lock_summary.excellent_rate_percent
        unlock_rate = current_result.unlock_summary.excellent_rate_percent
        summary = QLabel(
            "\n".join((
                f"有效数据：{self.readiness.eligible_directions}/8 个方向，"
                f"{self.readiness.eligible_datasets}/{self.readiness.total_datasets} 个完整方向组",
                f"当前闭锁优秀率：{_rate(lock_rate)}",
                f"当前解锁优秀率：{_rate(unlock_rate)}",
                "",
                f"搜索范围：当前非零阈值 ±{self.config.threshold_radius_db} dB，步长 1 dB",
                f"安全间隔：U-L ≥ {self.config.minimum_gap_db} dB",
                f"目标：闭锁、解锁优秀率分别 ≥{self.config.minimum_excellent_rate_percent:g}%",
                f"保护：不新增解锁距离 <{self.config.minimum_new_unlock_distance_m:g}m，"
                "且每组闭锁距离必须大于解锁距离",
            ))
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        notices = []
        notices.extend(f"错误：{message}" for message in self.readiness.errors)
        notices.extend(f"提示：{message}" for message in self.readiness.warnings)
        notices.extend(self.readiness.skipped_labels)
        self.precheck_details = QPlainTextEdit("\n".join(notices))
        self.precheck_details.setReadOnly(True)
        self.precheck_details.setMaximumHeight(170)
        self.precheck_details.setVisible(bool(notices))
        layout.addWidget(self.precheck_details)

        self.strategy_checkbox = QCheckBox(
            "基础阈值无法满足时，逐个尝试单一附加策略"
        )
        self.strategy_checkbox.setChecked(self.config.allow_strategy_fallback)
        layout.addWidget(self.strategy_checkbox)
        layout.addStretch()

        actions = QHBoxLayout()
        actions.addStretch()
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        self.start_button = QPushButton("开始优化")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setEnabled(self.readiness.can_start)
        self.start_button.clicked.connect(
            lambda: self.start_requested.emit(self.strategy_checkbox.isChecked())
        )
        actions.addWidget(self.start_button)
        layout.addLayout(actions)
        return page

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.progress_status = QLabel("准备开始……")
        self.progress_status.setWordWrap(True)
        layout.addWidget(self.progress_status)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, self.config.maximum_evaluations)
        layout.addWidget(self.progress_bar)
        self.progress_metrics = QLabel("")
        self.progress_metrics.setObjectName("mutedLabel")
        layout.addWidget(self.progress_metrics)
        layout.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_button = QPushButton("取消优化")
        self.cancel_button.clicked.connect(self._request_cancel)
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)
        return page

    def _build_result_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.result_status = QLabel("")
        self.result_status.setWordWrap(True)
        self.result_status.setMinimumHeight(44)
        layout.addWidget(self.result_status)
        self.result_summary = QLabel("")
        self.result_summary.setWordWrap(True)
        self.result_summary.setMinimumHeight(58)
        layout.addWidget(self.result_summary)

        self.threshold_table = QTableWidget(0, 5)
        self.threshold_table.setHorizontalHeaderLabels(
            ["节点", "当前闭锁 / 解锁", "推荐闭锁 / 解锁", "变化量", "推荐间隔"]
        )
        self.threshold_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.threshold_table.setMaximumHeight(190)
        layout.addWidget(self.threshold_table)

        self.sample_table = QTableWidget(0, 8)
        self.sample_table.setHorizontalHeaderLabels(
            [
                "方向组", "当前闭锁", "推荐闭锁", "当前解锁", "推荐解锁",
                "闭锁评级", "解锁评级", "推荐触发",
            ]
        )
        self.sample_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.sample_table.horizontalHeader().setStretchLastSection(True)
        self.sample_table.setAlternatingRowColors(True)
        layout.addWidget(self.sample_table)

        self.violation_details = QPlainTextEdit()
        self.violation_details.setReadOnly(True)
        self.violation_details.setMaximumHeight(100)
        layout.addWidget(self.violation_details)

        actions = QHBoxLayout()
        actions.addStretch()
        close_button = QPushButton("保持当前参数")
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        self.apply_button = QPushButton("应用到 What-if")
        self.apply_button.setObjectName("primaryButton")
        self.apply_button.clicked.connect(self._apply)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)
        return page

    def begin_progress(self) -> None:
        self._running = True
        self.stack.setCurrentWidget(self.progress_page)
        self.progress_status.setText("检查当前参数与完整方向组……")
        self.progress_bar.setValue(0)
        self.cancel_button.setEnabled(True)

    @Slot(object)
    def update_progress(self, progress: OptimizationProgress) -> None:
        self.progress_status.setText(progress.message)
        self.progress_bar.setValue(progress.evaluated_candidates)
        self.progress_metrics.setText(
            f"已评估 {progress.evaluated_candidates} 组候选 · "
            f"当前闭锁优秀率 {progress.best_lock_rate_percent:.1f}% · "
            f"解锁优秀率 {progress.best_unlock_rate_percent:.1f}%"
        )

    @Slot(object)
    def show_result(self, result: OptimizationResult) -> None:
        self._running = False
        self._result = result
        self.stack.setCurrentWidget(self.result_page)
        recommendation = result.recommendation
        baseline = result.baseline
        strategy = (
            "仅调整基础阈值，未启用附加策略"
            if recommendation.strategy_kind is None
            else f"基础阈值 + {STRATEGY_LABELS[recommendation.strategy_kind]}策略"
        )
        state = "通过全部约束" if result.can_apply else "未找到满足全部要求的方案"
        color = "#58d68d" if result.can_apply else "#ff6b78"
        self.result_status.setText(
            f"<span style='color:{color};font-size:16px;font-weight:700'>{state}</span>"
            f"<br>{result.stop_reason}"
        )
        self.result_summary.setText(
            f"方案类型：{strategy}　"
            f"数据覆盖：{result.readiness.eligible_directions}/8 方向、"
            f"{result.readiness.eligible_datasets} 个完整组"
            f"{'（低置信度）' if result.readiness.low_confidence else ''}<br>"
            f"闭锁优秀率：{baseline.metrics.lock_excellent_rate_percent:.1f}% → "
            f"{recommendation.metrics.lock_excellent_rate_percent:.1f}%　"
            f"解锁优秀率：{baseline.metrics.unlock_excellent_rate_percent:.1f}% → "
            f"{recommendation.metrics.unlock_excellent_rate_percent:.1f}%　"
            f"差/未触发："
            f"{baseline.metrics.lock_poor + baseline.metrics.unlock_poor} → "
            f"{recommendation.metrics.lock_poor + recommendation.metrics.unlock_poor}<br>"
            f"主节点间隔：{baseline.main_gap_db} dB → {recommendation.main_gap_db} dB　"
            f"±1 dB 稳定性：{result.robustness_passed}/{result.robustness_total}　"
            f"评估候选：{result.evaluated_candidates}　耗时：{result.elapsed_ms / 1000.0:.1f}s"
        )
        self._populate_thresholds(result)
        self._populate_samples(result)
        strategy_details = _strategy_parameters(recommendation.parameters)
        details = (
            "全部硬约束通过，可应用到 What-if 后继续人工检查和实车复验。"
            if result.can_apply
            else "失败原因：\n" + "\n".join(
                f"• {message}" for message in recommendation.violations
            )
        )
        if strategy_details:
            details += f"\n附加策略参数：{strategy_details}"
        self.violation_details.setPlainText(details)
        self.apply_button.setEnabled(result.can_apply)
        self.apply_button.setToolTip(
            "" if result.can_apply else "不合格预览不能应用到 What-if"
        )

    def show_error(self, message: str) -> None:
        self._running = False
        self.stack.setCurrentWidget(self.result_page)
        self.result_status.setText(
            "<span style='color:#ff6b78;font-size:16px;font-weight:700'>优化失败</span>"
        )
        self.result_summary.setText(message)
        self.threshold_table.hide()
        self.sample_table.hide()
        self.violation_details.hide()
        self.apply_button.setEnabled(False)

    def show_cancelled(self) -> None:
        self._running = False
        self.stack.setCurrentWidget(self.result_page)
        self.result_status.setText("自动优化已取消，当前 What-if 参数未发生变化。")
        self.result_summary.clear()
        self.threshold_table.hide()
        self.sample_table.hide()
        self.violation_details.hide()
        self.apply_button.setEnabled(False)

    def _populate_thresholds(self, result: OptimizationResult) -> None:
        current = result.baseline.parameters
        recommended = result.recommendation.parameters
        self.threshold_table.setRowCount(len(NODE_ORDER))
        for row, node in enumerate(NODE_ORDER):
            current_lock = current.lock_thresholds[node.index]
            current_unlock = current.unlock_thresholds[node.index]
            lock = recommended.lock_thresholds[node.index]
            unlock = recommended.unlock_thresholds[node.index]
            values = (
                node.label,
                f"{current_lock} / {current_unlock}",
                f"{lock} / {unlock}",
                f"{lock - current_lock:+d} / {unlock - current_unlock:+d}",
                "禁用" if lock == 0 and unlock == 0 else f"{unlock - lock} dB",
            )
            for column, value in enumerate(values):
                self.threshold_table.setItem(row, column, QTableWidgetItem(value))

    def _populate_samples(self, result: OptimizationResult) -> None:
        current = {sample.recording_id: sample for sample in result.baseline.samples}
        recommended = result.recommendation.samples
        self.sample_table.setRowCount(len(recommended))
        for row, sample in enumerate(recommended):
            baseline = current[sample.recording_id]
            triggers = []
            if sample.lock_trigger_node is not None:
                strategy = "--" if sample.lock_strategy is None else sample.lock_strategy.value
                triggers.append(f"闭:{sample.lock_trigger_node.label}/{strategy}")
            if sample.unlock_trigger_node is not None:
                strategy = "--" if sample.unlock_strategy is None else sample.unlock_strategy.value
                triggers.append(f"解:{sample.unlock_trigger_node.label}/{strategy}")
            trigger = "；".join(triggers) or "--"
            values = (
                sample.label,
                _distance(baseline.lock_distance_m),
                _distance(sample.lock_distance_m),
                _distance(baseline.unlock_distance_m),
                _distance(sample.unlock_distance_m),
                sample.lock_grade.label,
                sample.unlock_grade.label,
                trigger,
            )
            for column, value in enumerate(values):
                self.sample_table.setItem(row, column, QTableWidgetItem(value))

    def _request_cancel(self) -> None:
        self.cancel_button.setEnabled(False)
        self.progress_status.setText("正在安全停止……")
        self.cancel_requested.emit()

    def _apply(self) -> None:
        if self._result is not None and self._result.can_apply:
            self.apply_requested.emit(self._result)

    def reject(self) -> None:
        if self._running:
            self._request_cancel()
            return
        super().reject()


def _rate(value: Optional[float]) -> str:
    return "--" if value is None else f"{value:.1f}%"


def _distance(value: Optional[float]) -> str:
    return "未触发" if value is None else f"{value:.2f}m"


def _strategy_parameters(parameters: CloudParameters) -> str:
    values = []
    if parameters.mst_unlock and any(parameters.mst_unlock):
        values.append(
            "mstUnlock=" + "/".join(str(value) for value in parameters.mst_unlock)
        )
    for name, mapping in (
        ("quickLock", parameters.quick_lock),
        ("quickUnlock", parameters.quick_unlock),
        ("mstThanSlave", parameters.mst_than_slave),
        ("bevelAngle", parameters.bevel_angle),
    ):
        if not mapping:
            continue
        enabled = tuple(
            f"{field}={int(value)}"
            for field, value in mapping.items()
            if int(value) != 0
        )
        if enabled:
            values.append(f"{name}({', '.join(enabled)})")
    return "；".join(values)
