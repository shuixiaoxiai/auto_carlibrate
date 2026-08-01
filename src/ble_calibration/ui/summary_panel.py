"""Lock/unlock quality summary cards."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..analysis import QualitySummary, RecomputeResult


def _rate_text(value: Optional[float]) -> str:
    return "--" if value is None else f"{value:.1f}%"


def _directions_text(summary: QualitySummary, kind: str) -> str:
    directions = (
        summary.good_directions if kind == "good" else summary.poor_directions
    )
    untriggered = set(summary.untriggered_directions)
    if not directions:
        return "无"
    triggered_labels = [
        direction.label for direction in directions if direction not in untriggered
    ]
    untriggered_labels = [
        direction.label for direction in directions if direction in untriggered
    ]
    parts = []
    if triggered_labels:
        parts.append("、".join(triggered_labels))
    if untriggered_labels:
        parts.append(f"未触发：{'、'.join(untriggered_labels)}")
    return "；".join(parts)


class QualityCard(QFrame):
    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("summaryCard")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.setMinimumHeight(145)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        self.title_label = QLabel(title)
        self.title_label.setObjectName("sectionTitle")
        top.addWidget(self.title_label)
        top.addStretch()
        top.addWidget(QLabel("优秀率"))
        self.rate_label = QLabel("--")
        self.rate_label.setObjectName("metricValue")
        top.addWidget(self.rate_label)
        layout.addLayout(top)

        metrics = QHBoxLayout()
        self.excellent_label = QLabel("优 --")
        self.excellent_label.setObjectName("excellentMetric")
        self.good_label = QLabel("良 --")
        self.good_label.setObjectName("goodMetric")
        self.poor_label = QLabel("差 --")
        self.poor_label.setObjectName("poorMetric")
        metrics.addWidget(self.excellent_label)
        metrics.addWidget(self.good_label)
        metrics.addWidget(self.poor_label)
        metrics.addStretch()
        layout.addLayout(metrics)

        detail = QGridLayout()
        detail.addWidget(QLabel("良数据"), 0, 0)
        self.good_directions_label = QLabel("无")
        self.good_directions_label.setWordWrap(True)
        detail.addWidget(self.good_directions_label, 0, 1)
        detail.addWidget(QLabel("差数据"), 1, 0)
        self.poor_directions_label = QLabel("无")
        self.poor_directions_label.setWordWrap(True)
        detail.addWidget(self.poor_directions_label, 1, 1)
        detail.setColumnStretch(1, 1)
        layout.addLayout(detail)

    def set_summary(self, summary: QualitySummary) -> None:
        self.rate_label.setText(_rate_text(summary.excellent_rate_percent))
        self.excellent_label.setText(f"优 {summary.excellent}")
        self.good_label.setText(f"良 {summary.good}")
        self.poor_label.setText(f"差 {summary.poor}")
        self.good_directions_label.setText(_directions_text(summary, "good"))
        self.poor_directions_label.setText(_directions_text(summary, "poor"))


class SummaryPanel(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.lock_card = QualityCard("闭锁统计")
        self.unlock_card = QualityCard("解锁统计")
        layout.addWidget(self.lock_card)
        layout.addWidget(self.unlock_card)

    def set_result(self, result: RecomputeResult) -> None:
        self.lock_card.set_summary(result.lock_summary)
        self.unlock_card.set_summary(result.unlock_summary)
