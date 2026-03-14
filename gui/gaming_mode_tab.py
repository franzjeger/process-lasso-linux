"""Gaming Mode tab: CPU parking + profiles + Steam launcher + auto-restore."""
from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QPushButton, QTextEdit, QCheckBox, QMessageBox, QFrame,
    QInputDialog, QLineEdit, QGridLayout, QScrollArea, QComboBox,
)
from PyQt6.QtCore import pyqtSignal, QThread, QObject, pyqtSlot, Qt, QTimer, QProcess
from PyQt6.QtGui import QFont

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
    config_changed     = pyqtSignal(dict)       # emitted when gaming profiles are saved/deleted

    def __init__(self, config: dict = None, parent=None):
        super().__init__(parent)
        self._config = config or {}
        self._topo = None
        self._parked = False
        self._worker = None
        self._preferred_cbs: dict[int, QCheckBox] = {}
        self._smt_siblings: set[int] = set()
        # Launcher / auto-restore state
        self._launched_name: str = ""
        self._launched_pid: int | None = None
        self._watch_phase: str = "idle"   # idle | waiting | running
        self._watch_timer: QTimer | None = None
        self._pending_enable_after_unpark: bool = False
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

        # ── Game Launcher (with integrated profiles) ───────────────────────
        launcher_group = QGroupBox("Game Launcher")
        launcher_layout = QVBoxLayout(launcher_group)

        # Profile row
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Profile:"))
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(160)
        self._profile_combo.setToolTip(
            "Selecting a profile instantly restores the game name,\n"
            "launch command, and CPU parking settings saved with it."
        )
        profile_row.addWidget(self._profile_combo)
        save_profile_btn = QPushButton("Save")
        save_profile_btn.setToolTip("Save current game name, command, and CPU settings as a named profile")
        del_profile_btn  = QPushButton("Delete")
        save_profile_btn.clicked.connect(self._save_profile)
        del_profile_btn.clicked.connect(self._delete_profile)
        for b in [save_profile_btn, del_profile_btn]:
            profile_row.addWidget(b)
        profile_row.addStretch()
        launcher_layout.addLayout(profile_row)

        # Game name + source pickers
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Game:"))
        self._game_name_edit = QLineEdit()
        self._game_name_edit.setPlaceholderText("Game name (used to detect running process)")
        self._game_name_edit.textChanged.connect(self._on_game_fields_changed)
        name_row.addWidget(self._game_name_edit)
        pick_steam_btn = QPushButton("Steam…")
        pick_steam_btn.setToolTip("Browse your Steam library")
        pick_steam_btn.clicked.connect(self._pick_steam_game)
        pick_lutris_btn = QPushButton("Lutris…")
        pick_lutris_btn.setToolTip("Browse your Lutris library")
        pick_lutris_btn.clicked.connect(self._pick_lutris_game)
        name_row.addWidget(pick_steam_btn)
        name_row.addWidget(pick_lutris_btn)
        launcher_layout.addLayout(name_row)

        # Launch command
        cmd_row = QHBoxLayout()
        cmd_row.addWidget(QLabel("Command:"))
        self._cmd_edit = QLineEdit()
        self._cmd_edit.setPlaceholderText(
            "e.g.  steam -applaunch 238960   or   lutris lutris:rungame/slug   or   /path/to/game"
        )
        self._cmd_edit.setToolTip(
            "Any shell command to launch the game.\n"
            "The Steam and Lutris picker buttons fill this in automatically.\n"
            "You can also type or paste any command here."
        )
        self._cmd_edit.textChanged.connect(self._on_game_fields_changed)
        cmd_row.addWidget(self._cmd_edit)
        launcher_layout.addLayout(cmd_row)

        # Launch + auto-restore
        launch_row = QHBoxLayout()
        self._launch_btn = QPushButton("▶  Launch")
        self._launch_btn.setEnabled(False)
        self._launch_btn.setMinimumHeight(36)
        font2 = QFont()
        font2.setBold(True)
        self._launch_btn.setFont(font2)
        self._launch_btn.clicked.connect(self._launch_with_gaming_mode)
        launch_row.addWidget(self._launch_btn)

        self._auto_restore_cb = QCheckBox("Auto-disable Gaming Mode when game exits")
        self._auto_restore_cb.setChecked(True)
        self._auto_restore_cb.setToolTip(
            "Watches /proc for the launched game process.\n"
            "When the game exits, Gaming Mode is automatically disabled\n"
            "and all parked CPUs come back online."
        )
        launch_row.addWidget(self._auto_restore_cb)
        launch_row.addStretch()
        launcher_layout.addLayout(launch_row)

        kill_row = QHBoxLayout()
        self._kill_game_btn = QPushButton("⏹ Kill Game")
        self._kill_game_btn.setEnabled(False)
        self._kill_game_btn.clicked.connect(self._kill_launched)
        kill_row.addWidget(self._kill_game_btn)
        self._watch_status_label = QLabel("")
        self._watch_status_label.setStyleSheet("color: #a6e3a1;")
        kill_row.addWidget(self._watch_status_label)
        kill_row.addStretch()
        launcher_layout.addLayout(kill_row)

        layout.addWidget(launcher_group)
        self._refresh_profiles_combo()
        self._profile_combo.currentTextChanged.connect(self._load_profile)

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
        # Refresh topology + checkbox grid now that all CPUs are back online
        self._detect_topology()
        # Profile load requested a re-enable after unpark
        if self._pending_enable_after_unpark:
            self._pending_enable_after_unpark = False
            self._enable_gaming_mode()

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

        # Notify MonitorThread to restore nice values before anything else
        if self._parked:
            self.gaming_mode_changed.emit(False, False)
            self._parked = False
            self._park_btn.setText("▶  Enable Gaming Mode (Park non-preferred CPUs)")
            self._park_btn.setStyleSheet("")

        # Unpark CPUs
        if cpu_park.get_offline_cpus():
            self._append_log("[Reset] Unparking CPUs…")
            cpu_park.unpark_all(log_cb=self._append_log)
            self._update_cpu_status()
            # Refresh topology grid now that all CPUs are back online
            self._detect_topology()

        # Restore per-process affinities via monitor
        self.reset_requested.emit()

    # ── Profiles ──────────────────────────────────────────────────────────
    # A profile stores the complete game launch configuration:
    #   game_name  — display name / process-watch name
    #   command    — the shell command used to launch the game
    #   cpu_states — dict of str(cpu_index) → bool (which preferred CPUs to keep online)
    #   elevate_nice — whether to enable nice -1 elevation

    def _refresh_profiles_combo(self, select: str = ""):
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        profiles = self._config.get("gaming_mode", {}).get("profiles", {})
        for name in sorted(profiles):
            self._profile_combo.addItem(name)
        if select:
            idx = self._profile_combo.findText(select)
            if idx >= 0:
                self._profile_combo.setCurrentIndex(idx)
        self._profile_combo.blockSignals(False)

    def _save_profile(self):
        # Default the profile name to the current game name
        default = self._game_name_edit.text().strip() or ""
        name, ok = QInputDialog.getText(
            self, "Save Profile", "Profile name:", text=default
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        profile = {
            "game_name":    self._game_name_edit.text().strip(),
            "command":      self._cmd_edit.text().strip(),
            "cpu_states":   {str(cpu): cb.isChecked() for cpu, cb in self._preferred_cbs.items()},
            "elevate_nice": self._nice_cb.isChecked(),
        }
        self._config.setdefault("gaming_mode", {}).setdefault("profiles", {})[name] = profile
        self.config_changed.emit(self._config)
        self._refresh_profiles_combo(select=name)
        self._append_log(f"[Profile] Saved '{name}' → {profile['command']}")

    def _load_profile(self):
        name = self._profile_combo.currentText()
        if not name:
            return
        profile = self._config.get("gaming_mode", {}).get("profiles", {}).get(name)
        if not profile:
            self._append_log(f"[Profile] '{name}' not found.")
            return

        # Restore game name + command
        self._game_name_edit.setText(profile.get("game_name", ""))
        self._cmd_edit.setText(profile.get("command", ""))

        # Restore nice preference
        if "elevate_nice" in profile:
            self._nice_cb.setChecked(profile["elevate_nice"])

        # Restore CPU checkbox states (keys stored as strings)
        cpu_states = profile.get("cpu_states", {})
        for cpu, cb in self._preferred_cbs.items():
            cb.setChecked(cpu_states.get(str(cpu), cpu_states.get(cpu, True)))

        self._append_log(f"[Profile] Loaded '{name}' — {profile.get('command', '?')}")

        # If gaming mode is already active, re-apply parking with the restored CPU selection
        if self._parked:
            self._append_log(f"[Profile] Re-applying CPU parking for '{name}'…")
            self._disable_gaming_mode()
            self._pending_enable_after_unpark = True

    def _delete_profile(self):
        name = self._profile_combo.currentText()
        if not name:
            return
        ans = QMessageBox.question(
            self, "Delete Profile", f"Delete profile '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        self._config.get("gaming_mode", {}).get("profiles", {}).pop(name, None)
        self.config_changed.emit(self._config)
        self._refresh_profiles_combo()

    # ── Launcher helpers ────────────────────────────────────────────────────

    def _on_game_fields_changed(self):
        """Enable Launch button when both name and command are non-empty."""
        has_name = bool(self._game_name_edit.text().strip())
        has_cmd  = bool(self._cmd_edit.text().strip())
        self._launch_btn.setEnabled(has_name and has_cmd)

    def _pick_steam_game(self):
        from gui.dialogs import SteamGamePickerDialog
        dlg = SteamGamePickerDialog(self)
        if dlg.exec() == SteamGamePickerDialog.DialogCode.Accepted:
            appid, name = dlg.get_selection()
            if appid:
                self._game_name_edit.setText(name)
                self._cmd_edit.setText(f"steam -applaunch {appid}")

    def _pick_lutris_game(self):
        from gui.dialogs import LutrisGamePickerDialog
        dlg = LutrisGamePickerDialog(self)
        if dlg.exec() == LutrisGamePickerDialog.DialogCode.Accepted:
            slug, name = dlg.get_selection()
            if slug:
                self._game_name_edit.setText(name)
                self._cmd_edit.setText(f"lutris lutris:rungame/{slug}")

    def _launch_with_gaming_mode(self):
        cmd = self._cmd_edit.text().strip()
        name = self._game_name_edit.text().strip()
        if not cmd or not name:
            return
        if not self._parked:
            self._enable_gaming_mode()
        self._launched_name = name
        self._append_log(f"[Launcher] Launching '{name}': {cmd}")
        self._watch_phase = "waiting"
        self._launched_pid = None
        self._watch_status_label.setText("Waiting for game process…")
        self._kill_game_btn.setEnabled(True)

        import shlex
        try:
            parts = shlex.split(cmd)
        except ValueError:
            parts = cmd.split()
        proc = QProcess(self)
        proc.setProgram(parts[0])
        proc.setArguments(parts[1:])
        proc.finished.connect(self._on_launcher_exited)
        proc.start()

        # Start polling timer — launcher command often exits in ~1s, real game appears after
        if self._watch_timer:
            self._watch_timer.stop()
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(2000)
        self._watch_timer.timeout.connect(self._poll_game_process)
        self._watch_timer.start()

    @pyqtSlot(int, QProcess.ExitStatus)
    def _on_launcher_exited(self, exit_code: int, exit_status: QProcess.ExitStatus):
        # steam -applaunch exits immediately; real game is a child process
        self._append_log(f"[Launcher] steam process exited (code {exit_code}) — watching /proc for game…")

    @staticmethod
    def _proc_name_matches(game_name: str, pid: int) -> bool:
        """Return True if the process at *pid* looks like it matches *game_name*.

        Normalises both sides by lowercasing and stripping all non-alphanumeric
        characters so that "Path of Exile" matches comm "PathOfExile" (or the
        15-char truncated variant).  Falls back to checking /proc/pid/cmdline.
        """
        import re
        def norm(s: str) -> str:
            return re.sub(r'[^a-z0-9]', '', s.lower())

        name_n = norm(game_name)
        try:
            comm = open(f"/proc/{pid}/comm").read().strip()
            comm_n = norm(comm)
            if name_n in comm_n or comm_n in name_n:
                return True
        except OSError:
            return False
        # Secondary check: cmdline (handles Proton/Wine wrappers that forward the game exe)
        try:
            cmdline = open(f"/proc/{pid}/cmdline").read().replace('\x00', ' ')
            if name_n in norm(cmdline):
                return True
        except OSError:
            pass
        return False

    def _poll_game_process(self):
        """Poll /proc every 2s for the launched game process."""
        try:
            pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
        except OSError:
            return

        if self._watch_phase == "waiting":
            # Look for a process matching the game name
            for pid in pids:
                if self._proc_name_matches(self._launched_name, pid):
                    try:
                        comm = open(f"/proc/{pid}/comm").read().strip()
                    except OSError:
                        comm = "?"
                    self._launched_pid = pid
                    self._watch_phase = "running"
                    self._watch_status_label.setText(f"Game running (PID {pid})")
                    self._append_log(f"[Launcher] Game process found: PID {pid} ({comm})")
                    # Slow down poll interval once running
                    if self._watch_timer:
                        self._watch_timer.setInterval(5000)
                    return

        elif self._watch_phase == "running":
            # Check if the tracked process is still alive
            if self._launched_pid and self._launched_pid not in pids:
                # Original PID gone — check if a matching process is still running
                # (handles games that restart themselves or have multiple stages)
                replacement = next(
                    (p for p in pids if self._proc_name_matches(self._launched_name, p)),
                    None,
                )
                if replacement:
                    self._launched_pid = replacement
                    try:
                        comm = open(f"/proc/{replacement}/comm").read().strip()
                    except OSError:
                        comm = "?"
                    self._append_log(f"[Launcher] Game PID changed → {replacement} ({comm})")
                    return
                # No matching process found — game has exited
                self._append_log(f"[Launcher] Game process (PID {self._launched_pid}) exited.")
                if self._auto_restore_cb.isChecked():
                    self._stop_watch(restore=True)
                else:
                    self._stop_watch(restore=False)

    def _stop_watch(self, restore: bool):
        if self._watch_timer:
            self._watch_timer.stop()
            self._watch_timer = None
        self._watch_phase = "idle"
        self._launched_pid = None
        self._kill_game_btn.setEnabled(False)
        self._watch_status_label.setText("")
        if restore and self._parked:
            self._append_log("[Launcher] Auto-restoring: disabling Gaming Mode…")
            self._disable_gaming_mode()

    def _kill_launched(self):
        if self._launched_pid:
            try:
                import signal
                os.kill(self._launched_pid, signal.SIGTERM)
                self._append_log(f"[Launcher] Sent SIGTERM to PID {self._launched_pid}")
            except OSError as e:
                self._append_log(f"[Launcher] Kill failed: {e}")
        self._stop_watch(restore=self._auto_restore_cb.isChecked())

    @pyqtSlot(str)
    def _append_log(self, msg: str):
        self._log.append(msg)
        self.log_message.emit(msg)
