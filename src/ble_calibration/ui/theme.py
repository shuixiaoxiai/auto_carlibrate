"""Visual constants for the desktop calibration workspace."""

APP_STYLESHEET = """
QMainWindow, QWidget {
    background: #08111f;
    color: #dbe8f7;
    font-family: "Microsoft YaHei UI", "PingFang SC", "Segoe UI";
    font-size: 13px;
}
QToolBar {
    background: #0d192a;
    border: 0;
    border-bottom: 1px solid #21344d;
    spacing: 8px;
    padding: 7px 10px;
}
QToolButton, QPushButton {
    background: #172840;
    border: 1px solid #2b4564;
    border-radius: 6px;
    padding: 7px 13px;
    color: #eaf3ff;
}
QToolButton:hover, QPushButton:hover {
    background: #213957;
    border-color: #4e719b;
}
QPushButton#primaryButton {
    background: #1976d2;
    border-color: #4299ee;
    font-weight: 600;
}
QPushButton#dangerButton {
    background: #7f2632;
    border-color: #b94a5a;
}
QFrame#parameterPanel, QFrame#directionCard, QFrame#summaryCard {
    background: #0d192a;
    border: 1px solid #203650;
    border-radius: 10px;
}
QLabel#sectionTitle {
    color: #f5f9ff;
    font-size: 17px;
    font-weight: 700;
}
QLabel#mutedLabel {
    color: #8ea4bd;
}
QLabel#metricValue {
    color: #ffffff;
    font-size: 22px;
    font-weight: 700;
}
QLabel#excellentMetric { color: #58d68d; font-weight: 700; }
QLabel#goodMetric { color: #f4c95d; font-weight: 700; }
QLabel#poorMetric { color: #ff6b78; font-weight: 700; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #081422;
    border: 1px solid #29415e;
    border-radius: 5px;
    padding: 5px;
    selection-background-color: #2366a8;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #4da3ff;
}
QGroupBox {
    border: 1px solid #243b57;
    border-radius: 7px;
    margin-top: 12px;
    padding-top: 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: #a9c4df;
}
QTabWidget::pane {
    border: 1px solid #243b57;
    border-radius: 7px;
    background: #0a1626;
}
QTabBar::tab {
    background: #11223a;
    color: #a9bfd7;
    border: 1px solid #243b57;
    padding: 7px 15px;
}
QTabBar::tab:selected {
    background: #1d3858;
    color: #ffffff;
    border-bottom-color: #4da3ff;
}
QHeaderView::section {
    background: #12233a;
    color: #a9bfd7;
    border: 0;
    border-right: 1px solid #263d58;
    padding: 6px;
}
QTableWidget {
    background: #0a1626;
    alternate-background-color: #0c1b2d;
    border: 1px solid #223a55;
    gridline-color: #21354c;
}
QScrollArea { border: 0; }
QScrollBar:vertical {
    background: #08111f;
    width: 12px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #29415e;
    min-height: 32px;
    border-radius: 6px;
}
QStatusBar {
    background: #0d192a;
    color: #94abc3;
}
"""

NODE_COLORS = (
    "#56B4E9",
    "#F5A742",
    "#60D394",
    "#C77DFF",
    "#FF6B6B",
)

GRADE_BRUSHES = {
    "excellent": (54, 211, 153, 48),
    "good": (255, 193, 7, 58),
    "poor": (255, 71, 87, 52),
}
