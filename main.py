#!/usr/bin/env python3
"""Process Lasso — Linux KDE process manager with ProBalance.

Entry point: wires all components and starts the Qt application.
"""
from __future__ import annotations

import sys
import os
import logging

# Add app directory to path so all modules resolve correctly
APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

DARK_THEME = """
/* ── Process Lasso — Catppuccin Mocha dark theme ─────────────────────── */

/* Base */
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-size: 13px;
}
QMainWindow, QDialog {
    background-color: #1e1e2e;
}

/* ── Tabs ────────────────────────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #313244;
    border-radius: 6px;
    background-color: #1e1e2e;
    top: -1px;
}
QTabBar::tab {
    background-color: #181825;
    color: #6c7086;
    border: 1px solid #313244;
    border-bottom: none;
    padding: 6px 18px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 500;
}
QTabBar::tab:selected {
    background-color: #1e1e2e;
    color: #89b4fa;
    border-bottom: 2px solid #89b4fa;
}
QTabBar::tab:hover:!selected {
    background-color: #313244;
    color: #cdd6f4;
}

/* ── Buttons ─────────────────────────────────────────────────────────── */
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 5px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #45475a;
    border-color: #89b4fa;
    color: #89b4fa;
}
QPushButton:pressed {
    background-color: #585b70;
    border-color: #89b4fa;
}
QPushButton:disabled {
    background-color: #181825;
    color: #45475a;
    border-color: #313244;
}

/* ── GroupBox ────────────────────────────────────────────────────────── */
QGroupBox {
    border: 1px solid #313244;
    border-radius: 8px;
    margin-top: 10px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 5px;
    color: #89b4fa;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 1px;
}

/* ── Process / data tables ───────────────────────────────────────────── */
QTableWidget {
    background-color: #181825;
    alternate-background-color: #1e1e2e;
    gridline-color: #313244;
    border: 1px solid #313244;
    border-radius: 6px;
    selection-background-color: #45475a;
    selection-color: #cdd6f4;
    outline: none;
}
QTableWidget::item {
    padding: 4px 8px;
    border: none;
}
QTableWidget::item:selected {
    background-color: #45475a;
    color: #cdd6f4;
}
QTableWidget::item:hover {
    background-color: #313244;
}
QHeaderView {
    background-color: #11111b;
    border: none;
}
QHeaderView::section {
    background-color: #11111b;
    color: #585b70;
    border: none;
    border-right: 1px solid #313244;
    border-bottom: 2px solid #313244;
    padding: 5px 10px;
    font-weight: 700;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
}
QHeaderView::section:hover {
    background-color: #313244;
    color: #89b4fa;
}
QHeaderView::section:first {
    border-top-left-radius: 6px;
}
QHeaderView::section:last {
    border-right: none;
    border-top-right-radius: 6px;
}

/* ── Text inputs ─────────────────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: #45475a;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #89b4fa;
}
QTextEdit, QPlainTextEdit {
    background-color: #11111b;
    color: #a6adc8;
    border: 1px solid #313244;
    border-radius: 5px;
    padding: 4px;
    selection-background-color: #45475a;
    font-family: monospace;
    font-size: 12px;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background-color: #313244;
    border: none;
    width: 18px;
    border-radius: 3px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #45475a;
}

/* ── Checkboxes ──────────────────────────────────────────────────────── */
QCheckBox {
    spacing: 8px;
    color: #cdd6f4;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #45475a;
    border-radius: 4px;
    background-color: #181825;
}
QCheckBox::indicator:checked {
    background-color: #89b4fa;
    border-color: #89b4fa;
    image: none;
}
QCheckBox::indicator:hover {
    border-color: #89b4fa;
}

/* ── Scrollbars ──────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background-color: #181825;
    width: 8px;
    border-radius: 4px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: #45475a;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background-color: #585b70; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background-color: #181825;
    height: 8px;
    border-radius: 4px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: #45475a;
    border-radius: 4px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background-color: #585b70; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ── ComboBox ────────────────────────────────────────────────────────── */
QComboBox {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 5px;
    padding: 5px 10px;
}
QComboBox:hover { border-color: #89b4fa; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #45475a;
    selection-background-color: #313244;
    border-radius: 5px;
    padding: 4px;
}

/* ── Frames ──────────────────────────────────────────────────────────── */
QFrame {
    border: none;
}
QFrame[frameShape="4"], QFrame[frameShape="5"] {
    background-color: #313244;
    max-height: 1px;
}

/* ── Labels ──────────────────────────────────────────────────────────── */
QLabel { color: #cdd6f4; }

/* ── Context / popup menus ───────────────────────────────────────────── */
QMenu {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 22px;
    border-radius: 5px;
}
QMenu::item:selected {
    background-color: #313244;
    color: #89b4fa;
}
QMenu::separator {
    height: 1px;
    background-color: #313244;
    margin: 3px 6px;
}

/* ── List widgets ────────────────────────────────────────────────────── */
QListWidget {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 5px;
    outline: none;
}
QListWidget::item {
    padding: 5px 10px;
    border-radius: 4px;
}
QListWidget::item:selected {
    background-color: #313244;
    color: #89b4fa;
}
QListWidget::item:hover { background-color: #313244; }

/* ── Dialog button boxes ─────────────────────────────────────────────── */
QDialogButtonBox QPushButton { min-width: 80px; }

/* ── Tooltips ────────────────────────────────────────────────────────── */
QToolTip {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    padding: 5px 10px;
    border-radius: 5px;
}

/* ── Splitters ───────────────────────────────────────────────────────── */
QSplitter::handle { background-color: #313244; }
"""


def main():
    # Required before QApplication on some platforms
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    app = QApplication(sys.argv)
    app.setApplicationName("Process Lasso")
    app.setApplicationDisplayName("Process Lasso")
    app.setOrganizationName("process-lasso")
    app.setStyleSheet(DARK_THEME)

    # Do NOT quit when main window is closed (close hides to tray)
    app.setQuitOnLastWindowClosed(False)

    from gui.main_window import MainWindow
    window = MainWindow(app)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
