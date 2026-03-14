"""Main window: 5-tab layout + system tray integration."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime

from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QSystemTrayIcon, QMenu, QApplication,
    QLineEdit, QLabel, QPushButton, QCheckBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QIcon, QAction, QCloseEvent, QKeySequence, QShortcut

import config as cfg_module
from rules import RuleEngine
from probalance import ProBalance
from monitor import MonitorThread
from gui.process_table import ProcessTable
from gui.rules_panel import RulesPanel
from gui.probalance_tab import ProBalanceTab
from gui.settings_tab import SettingsTab
from gui.gaming_mode_tab import GamingModeTab
from gui.cpu_bars import CpuBarsWidget, CpuHistoryWidget


class MainWindow(QMainWindow):
    def __init__(self, app: QApplication):
        super().__init__()
        self._app = app
        self._config = cfg_module.load()
        self._rule_engine = RuleEngine()
        self._rule_engine.load_rules(self._config.get("rules", []))
        self._probalance = ProBalance(self._config.get("probalance", {}))

        self.setWindowTitle("Process Lasso")
        self.setMinimumSize(860, 620)

        # Scale default window size to available screen real estate
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            w = max(1000, min(1500, int(avail.width() * 0.82)))
            h = max(700, min(1050, int(avail.height() * 0.84)))
        else:
            w, h = 1100, 820
        self.resize(w, h)

        # App icon
        _icon_path = "/usr/share/icons/hicolor/scalable/apps/process-lasso-linux.svg"
        if os.path.exists(_icon_path):
            _app_icon = QIcon.fromTheme("utilities-system-monitor")
            self.setWindowIcon(QIcon(_icon_path))

        self._build_ui()
        self._build_tray()
        self._start_monitor()

        # Apply opacity from config
        opacity = self._config.get("ui", {}).get("opacity", 100.0)
        self.setWindowOpacity(opacity / 100.0)

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

        # Tab 1: Processes (history + bars + filter + process table)
        proc_container = QWidget()
        proc_layout = QVBoxLayout(proc_container)
        proc_layout.setContentsMargins(0, 0, 0, 0)
        proc_layout.setSpacing(4)

        self._cpu_history = CpuHistoryWidget()
        proc_layout.addWidget(self._cpu_history)

        self._cpu_bars = CpuBarsWidget()
        proc_layout.addWidget(self._cpu_bars)

        # Filter row
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self._proc_filter = QLineEdit()
        self._proc_filter.setPlaceholderText("Process name or PID…")
        self._proc_filter.setClearButtonEnabled(True)
        filter_row.addWidget(self._proc_filter)
        filter_row.addStretch()
        proc_layout.addLayout(filter_row)

        self._proc_table = ProcessTable(
            rule_engine=self._rule_engine,
            log_callback=self._append_log,
        )
        self._proc_table.rule_add_requested.connect(self._on_rule_add_from_table)
        self._proc_table.affinity_manually_changed.connect(self._on_affinity_manual_change)
        self._proc_filter.textChanged.connect(self._proc_table.set_filter)
        proc_layout.addWidget(self._proc_table)

        # Ctrl+F shortcut to focus filter
        _shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        _shortcut.activated.connect(lambda: (
            self._tabs.setCurrentIndex(0),
            self._proc_filter.setFocus(),
            self._proc_filter.selectAll(),
        ))

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
        self._gaming_tab = GamingModeTab(self._config)
        self._gaming_tab.reset_requested.connect(self._on_reset_requested)
        self._gaming_tab.log_message.connect(self._append_log)
        self._gaming_tab.gaming_mode_changed.connect(self._on_gaming_mode_changed)
        self._gaming_tab.config_changed.connect(self._on_gaming_config_changed)
        self._tabs.addTab(self._gaming_tab, "Gaming Mode")

        # Tab 5: Settings
        self._settings_tab = SettingsTab(self._config)
        self._settings_tab.settings_changed.connect(self._on_settings_changed)
        self._tabs.addTab(self._settings_tab, "Settings")

        # Tab 6: Log
        log_widget = QWidget()
        log_layout = QVBoxLayout(log_widget)

        # Log toolbar
        log_toolbar = QHBoxLayout()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(lambda: self._log_edit.clear())
        log_toolbar.addWidget(clear_btn)
        self._log_autoscroll_cb = QCheckBox("Auto-scroll")
        self._log_autoscroll_cb.setChecked(True)
        log_toolbar.addWidget(self._log_autoscroll_cb)
        log_toolbar.addStretch()
        log_layout.addLayout(log_toolbar)

        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        log_layout.addWidget(self._log_edit)
        self._tabs.addTab(log_widget, "Log")

    def _build_tray(self):
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(self._app.windowIcon())
        self._tray.setToolTip("Process Lasso")

        menu = QMenu()
        show_action = QAction("Show / Hide", self)
        show_action.triggered.connect(self._toggle_window)

        self._tray_gaming_action = QAction("▶  Enable Gaming Mode", self)
        self._tray_gaming_action.triggered.connect(self._gaming_tab._toggle_gaming_mode)
        self._gaming_tab.gaming_mode_changed.connect(self._update_tray_gaming_action)

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self._quit_app)
        menu.addAction(show_action)
        menu.addAction(self._tray_gaming_action)
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
        self._monitor.cpu_snapshot_ready.connect(self._cpu_history.update_cpu)
        self._monitor.cpu_snapshot_ready.connect(self._on_cpu_for_tray)
        self._monitor.log_message.connect(self._append_log)
        self._monitor.start()

        # Sync Gaming Mode: if the app started with CPUs already parked the
        # gaming_mode_changed signal fired during GamingModeTab.__init__ before
        # the monitor existed — notify it now.
        if self._gaming_tab._parked:
            self._monitor.set_gaming_mode(True, self._gaming_tab._nice_cb.isChecked())

    @pyqtSlot(list)
    def _on_snapshot(self, snapshot: list):
        throttled = self._probalance.get_throttled_pids()
        self._proc_table.update_throttled(throttled)
        self._proc_table.update_snapshot(snapshot)
        count = len(snapshot)
        self._tabs.setTabText(0, f"Processes ({count})")
        pb_idx = self._tabs.indexOf(self._pb_tab)
        throttled_count = len(throttled)
        if throttled_count:
            self._tabs.setTabText(pb_idx, f"ProBalance ({throttled_count})")
        else:
            self._tabs.setTabText(pb_idx, "ProBalance")

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
        if self._log_autoscroll_cb.isChecked():
            sb = self._log_edit.verticalScrollBar()
            sb.setValue(sb.maximum())

    @pyqtSlot(bool, bool)
    def _update_tray_gaming_action(self, active: bool, _):
        if active:
            self._tray_gaming_action.setText("⏹  Disable Gaming Mode")
        else:
            self._tray_gaming_action.setText("▶  Enable Gaming Mode")

    @pyqtSlot(list)
    def _on_cpu_for_tray(self, percpu: list):
        avg = sum(percpu) / len(percpu) if percpu else 0.0
        self._tray.setToolTip(f"Process Lasso — CPU avg {avg:.0f}%")

    @pyqtSlot(dict)
    def _on_gaming_config_changed(self, cfg: dict):
        self._config = cfg
        self._save_config()

    def _on_rules_changed(self):
        self._save_config()
        # Re-apply rules + default to all running processes so that processes
        # previously matched by a now-deleted or changed rule don't stay stuck
        # with a stale affinity.
        self._monitor.reapply_all_defaults()

    @pyqtSlot(int)
    def _on_affinity_manual_change(self, pid: int):
        self._monitor.set_manual_affinity_override(pid, 30.0)

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
        if active:
            # Push the default/rule affinities onto all currently running
            # processes so they immediately use the now-online preferred CPUs.
            self._monitor.reapply_all_defaults()

    @pyqtSlot(dict)
    def _on_settings_changed(self, updated_config: dict):
        self._config = updated_config
        self._monitor.update_config(self._config)
        # Re-apply default affinity to all currently running processes immediately
        self._monitor.reapply_all_defaults()
        self._save_config()
        self._apply_theme()

    def _apply_theme(self):
        """Switch between custom dark theme and system theme."""
        use_system = self._config.get("ui", {}).get("use_system_theme", False)
        if use_system:
            self._app.setStyleSheet("")
        else:
            dark_css = self._app.property("pl_dark_theme_css") or ""
            self._app.setStyleSheet(dark_css)

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
