"""Main window: 5-tab layout + system tray integration."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QVBoxLayout,
    QTextEdit, QSystemTrayIcon, QMenu, QApplication,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QIcon, QAction, QCloseEvent

import config as cfg_module
from rules import RuleEngine
from probalance import ProBalance
from monitor import MonitorThread
from gui.process_table import ProcessTable
from gui.rules_panel import RulesPanel
from gui.probalance_tab import ProBalanceTab
from gui.settings_tab import SettingsTab
from gui.gaming_mode_tab import GamingModeTab
from gui.cpu_bars import CpuBarsWidget


class MainWindow(QMainWindow):
    def __init__(self, app: QApplication):
        super().__init__()
        self._app = app
        self._config = cfg_module.load()
        self._rule_engine = RuleEngine()
        self._rule_engine.load_rules(self._config.get("rules", []))
        self._probalance = ProBalance(self._config.get("probalance", {}))

        self.setWindowTitle("Process Lasso")
        self.setMinimumSize(900, 600)
        self.resize(1100, 700)

        self._build_ui()
        self._build_tray()
        self._start_monitor()

        if self._config.get("ui", {}).get("start_minimized", False):
            self.hide()
        else:
            self.show()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        # Tab 1: Processes (CPU bars + process table stacked)
        proc_container = QWidget()
        proc_layout = QVBoxLayout(proc_container)
        proc_layout.setContentsMargins(0, 0, 0, 0)
        proc_layout.setSpacing(4)

        self._cpu_bars = CpuBarsWidget()
        proc_layout.addWidget(self._cpu_bars)

        self._proc_table = ProcessTable(
            rule_engine=self._rule_engine,
            log_callback=self._append_log,
        )
        self._proc_table.rule_add_requested.connect(self._on_rule_add_from_table)
        proc_layout.addWidget(self._proc_table)

        self._tabs.addTab(proc_container, "Processes")

        # Tab 2: Rules
        self._rules_panel = RulesPanel(self._rule_engine)
        self._rules_panel.rules_changed.connect(self._on_rules_changed)
        self._tabs.addTab(self._rules_panel, "Rules")

        # Tab 3: ProBalance
        self._pb_tab = ProBalanceTab(self._config.get("probalance", {}))
        self._pb_tab.settings_changed.connect(self._on_pb_settings_changed)
        self._tabs.addTab(self._pb_tab, "ProBalance")

        # Tab 4: Gaming Mode
        self._gaming_tab = GamingModeTab()
        self._gaming_tab.reset_requested.connect(self._on_reset_requested)
        self._gaming_tab.log_message.connect(self._append_log)
        self._gaming_tab.gaming_mode_changed.connect(self._on_gaming_mode_changed)
        self._tabs.addTab(self._gaming_tab, "Gaming Mode")

        # Tab 5: Settings
        self._settings_tab = SettingsTab(self._config)
        self._settings_tab.settings_changed.connect(self._on_settings_changed)
        self._tabs.addTab(self._settings_tab, "Settings")

        # Tab 6: Log
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)
        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        log_layout.addWidget(self._log_edit)
        self._tabs.addTab(log_widget, "Log")

    def _build_tray(self):
        self._tray = QSystemTrayIcon(self)
        icon = QIcon.fromTheme("utilities-system-monitor")
        if icon.isNull():
            icon = QIcon.fromTheme("applications-system")
        if icon.isNull():
            icon = self._app.windowIcon()
        self._tray.setIcon(icon)
        self._tray.setToolTip("Process Lasso")

        menu = QMenu()
        show_action = QAction("Show / Hide", self)
        show_action.triggered.connect(self._toggle_window)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)

        # KDE tray timing: show after 1s delay
        QTimer.singleShot(1000, self._tray.show)

    def _start_monitor(self):
        self._monitor = MonitorThread(
            rule_engine=self._rule_engine,
            probalance=self._probalance,
            config=self._config,
        )
        self._monitor.process_snapshot_ready.connect(self._on_snapshot)
        self._monitor.cpu_snapshot_ready.connect(self._cpu_bars.update_cpu)
        self._monitor.log_message.connect(self._append_log)
        self._monitor.start()

    @pyqtSlot(list)
    def _on_snapshot(self, snapshot: list):
        throttled = self._probalance.get_throttled_pids()
        self._proc_table.update_throttled(throttled)
        self._proc_table.update_snapshot(snapshot)

    @pyqtSlot(str)
    def _append_log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_edit.append(f"[{ts}] {msg}")
        doc = self._log_edit.document()
        if doc.blockCount() > 2000:
            cursor = self._log_edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()

    def _on_rules_changed(self):
        self._save_config()

    @pyqtSlot(object)
    def _on_rule_add_from_table(self, rule):
        self._rules_panel.add_rule_direct(rule)
        self._tabs.setCurrentIndex(1)  # Switch to Rules tab

    @pyqtSlot(dict)
    def _on_pb_settings_changed(self, pb_cfg: dict):
        self._config["probalance"] = pb_cfg
        self._probalance.update_config(pb_cfg)
        self._monitor.update_config(self._config)
        self._save_config()

    @pyqtSlot()
    def _on_reset_requested(self):
        self._monitor.reset_all_affinities()

    @pyqtSlot(bool, bool)
    def _on_gaming_mode_changed(self, active: bool, elevate_nice: bool):
        self._monitor.set_gaming_mode(active, elevate_nice)

    @pyqtSlot(dict)
    def _on_settings_changed(self, updated_config: dict):
        self._config = updated_config
        self._monitor.update_config(self._config)
        # Re-apply default affinity to all currently running processes immediately
        self._monitor.reapply_all_defaults()
        self._save_config()

    def _save_config(self):
        self._config["rules"] = self._rule_engine.to_dict_list()
        cfg_module.save(self._config)

    def _toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_window()

    def _quit_app(self):
        self._save_config()
        self._monitor.stop()
        self._monitor.wait(3000)
        self._tray.hide()
        self._app.quit()

    def closeEvent(self, event: QCloseEvent):
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "Process Lasso",
            "Running in the system tray. Right-click the tray icon to quit.",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )
