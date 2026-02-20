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
/* ── Process Lasso — Glassmorphism Dark Purple theme ─────────────────── */

/* Base — translucent dark purple glass */
QWidget {
    background-color: rgba(10, 5, 22, 225);
    color: #e2d4f0;
    font-size: 13px;
}
QMainWindow {
    background-color: transparent;
}
QDialog {
    background-color: rgba(16, 8, 32, 235);
    border: 1px solid rgba(139, 92, 246, 0.35);
    border-radius: 10px;
}

/* ── Central widget glass panel ──────────────────────────────────────── */
QMainWindow > QWidget {
    background-color: rgba(12, 6, 26, 218);
    border: 1px solid rgba(109, 40, 217, 0.25);
    border-radius: 10px;
}

/* ── Tabs ────────────────────────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid rgba(109, 40, 217, 0.3);
    border-radius: 8px;
    background-color: rgba(14, 7, 28, 210);
    top: -1px;
}
QTabBar::tab {
    background-color: rgba(20, 10, 38, 180);
    color: rgba(167, 139, 250, 0.55);
    border: 1px solid rgba(109, 40, 217, 0.25);
    border-bottom: none;
    padding: 6px 18px;
    margin-right: 2px;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    font-weight: 500;
}
QTabBar::tab:selected {
    background-color: rgba(30, 14, 54, 210);
    color: #a78bfa;
    border-bottom: 2px solid #7c3aed;
}
QTabBar::tab:hover:!selected {
    background-color: rgba(45, 22, 72, 160);
    color: #c4b5fd;
}

/* ── Buttons ─────────────────────────────────────────────────────────── */
QPushButton {
    background-color: rgba(76, 29, 149, 0.45);
    color: #c4b5fd;
    border: 1px solid rgba(139, 92, 246, 0.45);
    border-radius: 6px;
    padding: 5px 14px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: rgba(109, 40, 217, 0.6);
    border-color: rgba(167, 139, 250, 0.8);
    color: #ede9fe;
}
QPushButton:pressed {
    background-color: rgba(124, 58, 237, 0.75);
    border-color: #a78bfa;
}
QPushButton:disabled {
    background-color: rgba(15, 8, 30, 0.4);
    color: rgba(109, 40, 217, 0.4);
    border-color: rgba(76, 29, 149, 0.25);
}

/* ── GroupBox ────────────────────────────────────────────────────────── */
QGroupBox {
    background-color: rgba(20, 10, 40, 0.6);
    border: 1px solid rgba(109, 40, 217, 0.3);
    border-radius: 10px;
    margin-top: 10px;
    padding: 10px 8px 8px 8px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 5px;
    color: #8b5cf6;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 1px;
}

/* ── Process / data tables ───────────────────────────────────────────── */
QTableWidget {
    background-color: rgba(10, 4, 22, 0.75);
    alternate-background-color: rgba(20, 10, 38, 0.6);
    gridline-color: rgba(76, 29, 149, 0.25);
    border: 1px solid rgba(109, 40, 217, 0.3);
    border-radius: 8px;
    selection-background-color: rgba(76, 29, 149, 0.55);
    selection-color: #ede9fe;
    outline: none;
}
QTableWidget::item {
    padding: 4px 8px;
    border: none;
}
QTableWidget::item:selected {
    background-color: rgba(76, 29, 149, 0.55);
    color: #ede9fe;
}
QTableWidget::item:hover {
    background-color: rgba(45, 22, 72, 0.5);
}
QHeaderView {
    background-color: rgba(7, 3, 16, 0.85);
    border: none;
}
QHeaderView::section {
    background-color: rgba(7, 3, 16, 0.85);
    color: rgba(139, 92, 246, 0.7);
    border: none;
    border-right: 1px solid rgba(76, 29, 149, 0.3);
    border-bottom: 2px solid rgba(109, 40, 217, 0.4);
    padding: 5px 10px;
    font-weight: 700;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
}
QHeaderView::section:hover {
    background-color: rgba(45, 22, 72, 0.5);
    color: #a78bfa;
}
QHeaderView::section:first { border-top-left-radius: 8px; }
QHeaderView::section:last  { border-right: none; border-top-right-radius: 8px; }

/* ── Text inputs ─────────────────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox {
    background-color: rgba(10, 4, 22, 0.8);
    color: #e2d4f0;
    border: 1px solid rgba(109, 40, 217, 0.35);
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: rgba(76, 29, 149, 0.55);
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: rgba(139, 92, 246, 0.8);
    background-color: rgba(16, 8, 32, 0.9);
}
QTextEdit, QPlainTextEdit {
    background-color: rgba(6, 3, 14, 0.85);
    color: #c4b5fd;
    border: 1px solid rgba(76, 29, 149, 0.3);
    border-radius: 6px;
    padding: 4px;
    selection-background-color: rgba(76, 29, 149, 0.55);
    font-family: monospace;
    font-size: 12px;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background-color: rgba(76, 29, 149, 0.4);
    border: none;
    width: 18px;
    border-radius: 3px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: rgba(109, 40, 217, 0.6);
}

/* ── Checkboxes ──────────────────────────────────────────────────────── */
QCheckBox {
    spacing: 8px;
    color: #e2d4f0;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid rgba(109, 40, 217, 0.5);
    border-radius: 4px;
    background-color: rgba(10, 4, 22, 0.7);
}
QCheckBox::indicator:checked {
    background-color: rgba(109, 40, 217, 0.85);
    border-color: #8b5cf6;
}
QCheckBox::indicator:hover {
    border-color: #a78bfa;
}

/* ── Scrollbars ──────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background-color: rgba(10, 4, 22, 0.5);
    width: 8px;
    border-radius: 4px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background-color: rgba(109, 40, 217, 0.5);
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background-color: rgba(139, 92, 246, 0.7); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background-color: rgba(10, 4, 22, 0.5);
    height: 8px;
    border-radius: 4px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background-color: rgba(109, 40, 217, 0.5);
    border-radius: 4px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background-color: rgba(139, 92, 246, 0.7); }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ── ComboBox ────────────────────────────────────────────────────────── */
QComboBox {
    background-color: rgba(10, 4, 22, 0.8);
    color: #e2d4f0;
    border: 1px solid rgba(109, 40, 217, 0.35);
    border-radius: 5px;
    padding: 5px 10px;
}
QComboBox:hover { border-color: rgba(139, 92, 246, 0.7); }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background-color: rgba(14, 7, 28, 0.95);
    color: #e2d4f0;
    border: 1px solid rgba(109, 40, 217, 0.4);
    selection-background-color: rgba(76, 29, 149, 0.55);
    border-radius: 6px;
    padding: 4px;
}

/* ── Frames ──────────────────────────────────────────────────────────── */
QFrame { border: none; }
QFrame[frameShape="4"], QFrame[frameShape="5"] {
    background-color: rgba(109, 40, 217, 0.25);
    max-height: 1px;
}

/* ── Labels ──────────────────────────────────────────────────────────── */
QLabel { color: #e2d4f0; }

/* ── Context / popup menus ───────────────────────────────────────────── */
QMenu {
    background-color: rgba(14, 7, 28, 240);
    color: #e2d4f0;
    border: 1px solid rgba(109, 40, 217, 0.45);
    border-radius: 8px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 22px;
    border-radius: 5px;
}
QMenu::item:selected {
    background-color: rgba(76, 29, 149, 0.55);
    color: #c4b5fd;
}
QMenu::separator {
    height: 1px;
    background-color: rgba(109, 40, 217, 0.3);
    margin: 3px 6px;
}

/* ── List widgets ────────────────────────────────────────────────────── */
QListWidget {
    background-color: rgba(10, 4, 22, 0.75);
    border: 1px solid rgba(109, 40, 217, 0.3);
    border-radius: 6px;
    outline: none;
}
QListWidget::item {
    padding: 5px 10px;
    border-radius: 4px;
}
QListWidget::item:selected {
    background-color: rgba(76, 29, 149, 0.55);
    color: #c4b5fd;
}
QListWidget::item:hover { background-color: rgba(45, 22, 72, 0.45); }

/* ── Dialog button boxes ─────────────────────────────────────────────── */
QDialogButtonBox QPushButton { min-width: 80px; }

/* ── Tooltips ────────────────────────────────────────────────────────── */
QToolTip {
    background-color: rgba(20, 10, 40, 240);
    color: #e2d4f0;
    border: 1px solid rgba(139, 92, 246, 0.5);
    padding: 5px 10px;
    border-radius: 6px;
}

/* ── Splitters ───────────────────────────────────────────────────────── */
QSplitter::handle { background-color: rgba(109, 40, 217, 0.25); }
"""


def main():
    # Required before QApplication on some platforms
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    app = QApplication(sys.argv)
    app.setApplicationName("Process Lasso")
    app.setApplicationDisplayName("Process Lasso")
    app.setOrganizationName("process-lasso")

    # Apply custom theme unless the user opted into the system theme
    import config as _cfg
    _startup_cfg = _cfg.load()
    if not _startup_cfg.get("ui", {}).get("use_system_theme", False):
        app.setStyleSheet(DARK_THEME)
    # Store theme CSS so MainWindow can toggle it without re-importing
    app.setProperty("pl_dark_theme_css", DARK_THEME)

    # Do NOT quit when main window is closed (close hides to tray)
    app.setQuitOnLastWindowClosed(False)

    from gui.main_window import MainWindow
    window = MainWindow(app)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
