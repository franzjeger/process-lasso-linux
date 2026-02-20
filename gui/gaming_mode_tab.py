"""Gaming Mode tab: CPU parking + reset all changes."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QTextEdit, QCheckBox, QMessageBox, QFrame,
    QInputDialog, QLineEdit, QGridLayout, QScrollArea,
)
from PyQt6.QtCore import pyqtSignal, QThread, QObject, pyqtSlot, Qt
from PyQt6.QtGui import QFont, QColor

import cpu_park


class _WorkerSignals(QObject):
    done = pyqtSignal(bool, str)  # success, message
    log  = pyqtSignal(str)


class _ParkWorker(QThread):
    """Run park/unpark in background thread to keep UI responsive."""
    def __init__(self, action: str, cpus: set[int] | None = None):
        super().__init__()
        self._action = action   # "park" | "unpark"
        self._cpus = cpus
        self.signals = _WorkerSignals()

    def run(self):
        logs = []
        def cb(msg): logs.append(msg)
        if self._action == "park":
            ok = cpu_park.park_cpus(self._cpus or set(), log_cb=cb)
        else:
            ok = cpu_park.unpark_all(log_cb=cb)
        for msg in logs:
            self.signals.log.emit(msg)
        self.signals.done.emit(ok, "\n".join(logs))


class GamingModeTab(QWidget):
    reset_requested    = pyqtSignal()           # → MonitorThread.reset_all_affinities()
    log_message        = pyqtSignal(str)
    gaming_mode_changed = pyqtSignal(bool, bool)  # active, elevate_nice → MonitorThread.set_gaming_mode()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._topo = None
        self._parked = False
        self._worker = None
        self._preferred_cbs: dict[int, QCheckBox] = {}
        self._smt_siblings: set[int] = set()
        self._build_ui()
        self._detect_topology()

    def _build_ui(self):
        # Wrap everything in a scroll area so the tab doesn't force window height
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner_widget = QWidget()
        scroll.setWidget(inner_widget)
        outer.addWidget(scroll)
        layout = QVBoxLayout(inner_widget)

        # ── CPU topology info ──────────────────────────────────────────────
        topo_group = QGroupBox("CPU Topology")
        topo_layout = QVBoxLayout(topo_group)
        self._topo_label = QLabel("Detecting…")
        self._topo_label.setWordWrap(True)
        topo_layout.addWidget(self._topo_label)
        layout.addWidget(topo_group)

        # ── Gaming Mode (CPU parking) ──────────────────────────────────────
        park_group = QGroupBox("Gaming Mode — CPU Parking")
        park_layout = QVBoxLayout(park_group)

        desc = QLabel(
            "Parks (takes offline) non-preferred CPUs so the game initialises its\n"
            "thread pool against the correct CPU count — no frametime jitter.\n\n"
            "This mirrors exactly what gamemoderun does:\n"
            "  AMD X3D  → parks non-V-Cache CCD (smaller L3)\n"
            "  Intel Hybrid → parks E-cores (lower max freq)\n"
            "  Uniform CPU → parking disabled (no asymmetry)"
        )
        desc.setWordWrap(True)
        park_layout.addWidget(desc)

        # ── Preferred CCD core selection ────────────────────────────────
        self._core_sel_group = QGroupBox("Preferred CCD — Active Cores in Gaming Mode")
        core_sel_layout = QVBoxLayout(self._core_sel_group)

        core_sel_info = QLabel(
            "All preferred-CCD CPUs are kept online by default.\n"
            "Uncheck any CPU to park it along with the non-preferred CCD.\n"
            "Use 'No SMT' to park the hyperthread siblings and run on physical cores only."
        )
        core_sel_info.setWordWrap(True)
        core_sel_layout.addWidget(core_sel_info)

        # Dynamic checkbox grid — populated in _detect_topology
        self._preferred_grid_widget = QWidget()
        self._preferred_grid_layout = QGridLayout(self._preferred_grid_widget)
        self._preferred_grid_layout.setHorizontalSpacing(8)
        self._preferred_grid_layout.setVerticalSpacing(3)
        self._preferred_grid_layout.setContentsMargins(0, 4, 0, 0)
        core_sel_layout.addWidget(self._preferred_grid_widget)

        quick_row = QHBoxLayout()
        all_btn  = QPushButton("All")
        self._no_smt_btn = QPushButton("No SMT (physical only)")
        none_btn = QPushButton("None")
        all_btn.setMaximumWidth(60)
        self._no_smt_btn.setMaximumWidth(160)
        none_btn.setMaximumWidth(60)
        all_btn.clicked.connect(lambda: self._select_preferred("all"))
        self._no_smt_btn.clicked.connect(lambda: self._select_preferred("no_smt"))
        none_btn.clicked.connect(lambda: self._select_preferred("none"))
        quick_row.addWidget(all_btn)
        quick_row.addWidget(self._no_smt_btn)
        quick_row.addWidget(none_btn)
        quick_row.addStretch()
        core_sel_layout.addLayout(quick_row)

        self._core_sel_group.setVisible(False)  # shown once topology is detected
        park_layout.addWidget(self._core_sel_group)

        helper_frame = QFrame()
        helper_frame.setFrameShape(QFrame.Shape.StyledPanel)
        helper_layout = QHBoxLayout(helper_frame)
        self._helper_status = QLabel()
        helper_layout.addWidget(self._helper_status)
        install_btn = QPushButton("Install / Update Helper (root)")
        install_btn.clicked.connect(self._install_helper)
        helper_layout.addWidget(install_btn)
        helper_layout.addStretch()
        park_layout.addWidget(helper_frame)

        self._nice_cb = QCheckBox("Elevate game priority (nice -1) — gives game processes higher scheduling priority")
        self._nice_cb.setChecked(True)
        self._nice_cb.setToolTip(
            "When Gaming Mode is active, apply nice -1 to all processes that match\n"
            "your configured rules. Mirrors gamemoded's priority elevation.\n"
            "Requires the privileged helper (nice < 0 needs root)."
        )
        park_layout.addWidget(self._nice_cb)

        btn_row = QHBoxLayout()
        self._park_btn = QPushButton("▶  Enable Gaming Mode (Park non-preferred CPUs)")
        self._park_btn.setMinimumHeight(40)
        font = QFont()
        font.setBold(True)
        self._park_btn.setFont(font)
        self._park_btn.clicked.connect(self._toggle_gaming_mode)
        btn_row.addWidget(self._park_btn)
        park_layout.addLayout(btn_row)

        self._cpu_status_label = QLabel()
        self._cpu_status_label.setWordWrap(True)
        park_layout.addWidget(self._cpu_status_label)
        layout.addWidget(park_group)

        # ── Reset All Changes ──────────────────────────────────────────────
        reset_group = QGroupBox("Reset All Changes")
        reset_layout = QVBoxLayout(reset_group)
        reset_desc = QLabel(
            "Restores all per-process CPU affinities that Process Lasso has changed\n"
            "back to their original state, and unparks any parked CPUs.\n"
            "Use this to cleanly undo everything without restarting."
        )
        reset_desc.setWordWrap(True)
        reset_layout.addWidget(reset_desc)

        reset_btn = QPushButton("↩  Reset All Changes")
        reset_btn.setMinimumHeight(36)
        reset_btn.clicked.connect(self._reset_all)
        reset_layout.addWidget(reset_btn)
        layout.addWidget(reset_group)

        # ── Log ───────────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(140)
        self._log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self._log)

        layout.addStretch()
        self._update_helper_status()
        self._update_cpu_status()

    def _detect_topology(self):
        self._topo = cpu_park.detect_topology()
        self._topo_label.setText(self._topo.description)
        has_asym = self._topo.has_asymmetry
        self._park_btn.setEnabled(has_asym and cpu_park.is_helper_installed())
        if not has_asym:
            self._park_btn.setText("Gaming Mode unavailable (uniform CPU topology)")
            self._core_sel_group.setVisible(False)
            return

        # Populate the preferred CCD checkbox grid
        self._core_sel_group.setVisible(True)
        preferred = sorted(self._topo.preferred)
        self._smt_siblings = cpu_park.get_smt_siblings_of(self._topo.preferred)

        # Clear any previous widgets
        self._preferred_cbs.clear()
        while self._preferred_grid_layout.count():
            item = self._preferred_grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        cols = 8
        offline = cpu_park.get_offline_cpus()
        for idx, cpu in enumerate(preferred):
            is_smt = cpu in self._smt_siblings
            label = f"CPU {cpu}" + (" (HT)" if is_smt else "")
            cb = QCheckBox(label)
            cb.setChecked(True)
            if is_smt:
                cb.setToolTip("SMT hyperthread sibling — uncheck to park (No SMT mode)")
            else:
                cb.setToolTip("Physical core of preferred CCD")
            if cpu in offline:
                cb.setChecked(False)
                cb.setToolTip(cb.toolTip() + " — currently parked")
            row, col = divmod(idx, cols)
            self._preferred_grid_layout.addWidget(cb, row, col)
            self._preferred_cbs[cpu] = cb

        # "No SMT" button only meaningful when SMT siblings exist
        self._no_smt_btn.setEnabled(bool(self._smt_siblings))

        # If CPUs are already parked (Gaming Mode was active before this session),
        # restore the active visual state so the user can disable it properly.
        if offline:
            self._parked = True
            self._park_btn.setText("⏹  Disable Gaming Mode (Unpark CPUs)")
            self._park_btn.setStyleSheet(
                "background-color: #1e4a2a; color: #a6e3a1; border: 1px solid #a6e3a1;"
            )
            # Tell MonitorThread Gaming Mode is active
            self.gaming_mode_changed.emit(True, self._nice_cb.isChecked())

    def _update_helper_status(self):
        if cpu_park.is_helper_current() and cpu_park.is_sudoers_installed():
            self._helper_status.setText("✓ Helper installed — parking + nice -1 available")
            self._helper_status.setStyleSheet("color: #a6e3a1;")
        elif cpu_park.is_helper_installed() and cpu_park.is_sudoers_installed():
            self._helper_status.setText("⚠ Helper needs update — click 'Install / Update Helper'")
            self._helper_status.setStyleSheet("color: #f9e2af;")
        else:
            self._helper_status.setText("✗ Helper not installed — click 'Install / Update Helper' to enable parking")
            self._helper_status.setStyleSheet("color: #f38ba8;")

    def _update_cpu_status(self):
        online  = cpu_park.get_online_cpus()
        offline = cpu_park.get_offline_cpus()
        if offline:
            self._cpu_status_label.setText(
                f"Online: {sorted(online)}  |  Offline (parked): {sorted(offline)}"
            )
            self._cpu_status_label.setStyleSheet("color: orange;")
        else:
            self._cpu_status_label.setText(f"All CPUs online: {sorted(online)}")
            self._cpu_status_label.setStyleSheet("")

    def _select_preferred(self, mode: str):
        """Quick-select helper for the preferred CCD checkboxes."""
        for cpu, cb in self._preferred_cbs.items():
            if mode == "all":
                cb.setChecked(True)
            elif mode == "none":
                cb.setChecked(False)
            elif mode == "no_smt":
                cb.setChecked(cpu not in self._smt_siblings)

    def _install_helper(self):
        password, ok = QInputDialog.getText(
            self, "Root Authentication",
            "Enter root password to install the privileged sysfs helper:",
            QLineEdit.EchoMode.Password,
        )
        if not ok or not password:
            return
        self._append_log("Installing privileged helper…")
        ok, msg = cpu_park.install_helper_as_root(password=password)
        self._append_log(msg)
        self._update_helper_status()
        if ok and self._topo and self._topo.has_asymmetry:
            self._park_btn.setEnabled(True)
        QMessageBox.information(self, "Install Helper", msg)

    def _toggle_gaming_mode(self):
        if self._parked:
            self._disable_gaming_mode()
        else:
            self._enable_gaming_mode()

    def _enable_gaming_mode(self):
        if not self._topo or not self._topo.has_asymmetry:
            return
        if not cpu_park.is_helper_installed():
            QMessageBox.warning(self, "Helper Missing", "Install the privileged helper first.")
            return
        # Non-preferred CCD always parked; also park any unchecked preferred CPUs
        unchecked_preferred = {cpu for cpu, cb in self._preferred_cbs.items() if not cb.isChecked()}
        cpus_to_park = self._topo.non_preferred | unchecked_preferred
        self._append_log(f"[Gaming Mode] Parking CPUs {sorted(cpus_to_park)}…")
        self._park_btn.setEnabled(False)
        self._worker = _ParkWorker("park", cpus_to_park)
        self._worker.signals.log.connect(self._append_log)
        self._worker.signals.done.connect(self._on_park_done)
        self._worker.start()

    def _disable_gaming_mode(self):
        self._append_log("[Gaming Mode] Unparking all CPUs…")
        self._park_btn.setEnabled(False)
        self._worker = _ParkWorker("unpark")
        self._worker.signals.log.connect(self._append_log)
        self._worker.signals.done.connect(self._on_unpark_done)
        self._worker.start()

    @pyqtSlot(bool, str)
    def _on_park_done(self, ok: bool, msg: str):
        self._parked = ok
        self._park_btn.setEnabled(True)
        self._update_cpu_status()
        if ok:
            self._park_btn.setText("⏹  Disable Gaming Mode (Unpark CPUs)")
            self._park_btn.setStyleSheet("background-color: #1e4a2a; color: #a6e3a1; border: 1px solid #a6e3a1;")
            self._append_log("[Gaming Mode] ACTIVE — non-preferred CPUs offline.")
            self.gaming_mode_changed.emit(True, self._nice_cb.isChecked())
        else:
            self._append_log("[Gaming Mode] Parking failed — check log.")
        self.log_message.emit(f"[Gaming Mode] {'enabled' if ok else 'FAILED'}")

    @pyqtSlot(bool, str)
    def _on_unpark_done(self, ok: bool, msg: str):
        self._parked = False
        self._park_btn.setEnabled(True)
        self._update_cpu_status()
        self._park_btn.setText("▶  Enable Gaming Mode (Park non-preferred CPUs)")
        self._park_btn.setStyleSheet("")
        self._append_log("[Gaming Mode] Disabled — all CPUs online.")
        self.gaming_mode_changed.emit(False, False)
        self.log_message.emit("[Gaming Mode] disabled")

    def _reset_all(self):
        ans = QMessageBox.question(
            self, "Reset All Changes",
            "This will:\n"
            "  • Restore all per-process CPU affinities to their original state\n"
            "  • Unpark any parked CPUs\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        # Unpark CPUs first
        if cpu_park.get_offline_cpus():
            self._append_log("[Reset] Unparking CPUs…")
            cpu_park.unpark_all(log_cb=self._append_log)
            self._parked = False
            self._park_btn.setText("▶  Enable Gaming Mode (Park non-preferred CPUs)")
            self._park_btn.setStyleSheet("")
            self._update_cpu_status()
            self.gaming_mode_changed.emit(False, False)

        # Restore per-process affinities via monitor
        self.reset_requested.emit()

    def _append_log(self, msg: str):
        self._log.append(msg)
        self.log_message.emit(msg)
