"""Project picker backed by the shared SQLite repository."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..storage import ProjectRepository


class ProjectPickerDialog(QDialog):
    def __init__(
        self,
        database_path: Path,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.database_path = Path(database_path)
        self.project_id: Optional[str] = None
        self.setWindowTitle("打开标定项目")
        self.resize(760, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"项目库：{self.database_path}"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["项目名称", "方向数", "更新时间", "原始附件"]
        )
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._accept_selected)
        layout.addWidget(self.table)

        with ProjectRepository(self.database_path) as repository:
            projects = repository.list_projects()
        self.table.setRowCount(len(projects))
        for row, project in enumerate(projects):
            name_item = QTableWidgetItem(project.name)
            name_item.setData(Qt.ItemDataRole.UserRole, project.project_id)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(str(project.direction_count)))
            self.table.setItem(row, 2, QTableWidgetItem(project.updated_at))
            self.table.setItem(
                row,
                3,
                QTableWidgetItem(project.capture_path or "分方向附件"),
            )
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(True)
        if projects:
            self.table.selectRow(0)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        self.project_id = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    @classmethod
    def pick(
        cls,
        database_path: Path,
        parent: Optional[QWidget] = None,
    ) -> Optional[str]:
        dialog = cls(database_path, parent)
        return (
            dialog.project_id
            if dialog.exec() == QDialog.DialogCode.Accepted
            else None
        )
