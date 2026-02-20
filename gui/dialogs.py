"""Dialogs for setting affinity, nice priority, and ionice priority."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QComboBox, QGridLayout, QCheckBox, QWidget,
    QScrollArea, QGroupBox, QDialogButtonBox, QMessageBox,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QLineEdit,
    QHeaderView,
)
from PyQt6.QtCore import Qt, QSortFilterProxyModel, QTimer
from PyQt6.QtGui import QStandardItemModel, QStandardItem
import utils


class AffinityDialog(QDialog):
    """CPU affinity picker: topology-aware checkbox grid."""

    def __init__(self, current_affinity: str = "", parent=None, title_suffix: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Set CPU Affinity{' — ' + title_suffix if title_suffix else ''}")
        # Always use total CPU count (present), not just online count
        self._cpu_count = utils.get_cpu_count()
        self._checkboxes: list[QCheckBox] = []
        self._build_ui(current_affinity)
        self.setMinimumWidth(520)
        self.adjustSize()

    def _build_ui(self, current_affinity: str):
        layout = QVBoxLayout(self)
        selected = self._parse_cpulist(current_affinity)

        # Gather topology info for CCD-aware display
        try:
            import cpu_park as _cp
            offline = _cp.get_offline_cpus()
            topo = _cp.detect_topology()
            smt_siblings = _cp.get_smt_siblings_of(set(range(self._cpu_count)))
        except Exception:
            offline = set()
            topo = None
            smt_siblings = set()

        preferred   = set(topo.preferred)     if (topo and topo.has_asymmetry) else set()
        non_pref    = set(topo.non_preferred)  if (topo and topo.has_asymmetry) else set()

        # cb_map[cpu] = QCheckBox for that CPU
        cb_map: dict[int, QCheckBox] = {}

        group = QGroupBox("Select CPUs")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        grid.setContentsMargins(8, 8, 8, 8)

        if preferred and non_pref:
            # ── Topology-aware: group by CCD + physical/HT ──────────────
            pref_phys  = sorted(c for c in preferred if c not in smt_siblings)
            pref_ht    = sorted(c for c in preferred if c in smt_siblings)
            npref_phys = sorted(c for c in non_pref  if c not in smt_siblings)
            npref_ht   = sorted(c for c in non_pref  if c in smt_siblings)

            from cpu_park import TopologyKind
            if topo and topo.kind == TopologyKind.AMD_X3D:
                ccd0_name, ccd1_name = "CCD0 (V-Cache — preferred)", "CCD1 (parked in Gaming Mode)"
            else:
                ccd0_name, ccd1_name = "Preferred CCD", "Non-preferred CCD (parked in Gaming Mode)"

            row = [0]

            def add_section(title: str, cpus: list[int]):
                hdr = QLabel(title)
                hdr.setStyleSheet(
                    "font-size: 11px; font-weight: 600; "
                    "color: rgba(167,139,250,0.9); padding-top: 6px;"
                )
                grid.addWidget(hdr, row[0], 0, 1, 8)
                row[0] += 1
                for col, cpu in enumerate(cpus):
                    cb = QCheckBox(str(cpu))
                    cb.setChecked(cpu in selected)
                    if cpu in offline:
                        cb.setEnabled(False)
                        cb.setToolTip(f"CPU {cpu} is parked — disable Gaming Mode to use it")
                    grid.addWidget(cb, row[0], col)
                    cb_map[cpu] = cb
                row[0] += 1

            if pref_phys:
                add_section(f"  {ccd0_name} — physical cores", pref_phys)
            if pref_ht:
                add_section(f"  {ccd0_name} — HT siblings", pref_ht)
            if npref_phys:
                add_section(f"  {ccd1_name} — physical cores", npref_phys)
            if npref_ht:
                add_section(f"  {ccd1_name} — HT siblings", npref_ht)

        else:
            # ── Flat grid fallback for uniform / unknown topology ────────
            cols = 8
            for i in range(self._cpu_count):
                cb = QCheckBox(str(i))
                cb.setChecked(i in selected)
                if i in offline:
                    cb.setEnabled(False)
                    cb.setToolTip(f"CPU {i} is currently parked")
                r, c = divmod(i, cols)
                grid.addWidget(cb, r, c)
                cb_map[i] = cb

        # Build ordered list: self._checkboxes[i] = checkbox for CPU i
        self._checkboxes = [cb_map.get(i, QCheckBox(str(i))) for i in range(self._cpu_count)]

        if offline:
            offline_str = utils._cpuset_to_cpulist(offline)
            note = QLabel(f"⚠  CPUs {offline_str} are parked (Gaming Mode active) — unpark to select them.")
            note.setStyleSheet("color: rgba(249,226,175,0.85); font-size: 11px;")
            note.setWordWrap(True)
            layout.addWidget(note)

        layout.addWidget(group)

        btn_row = QHBoxLayout()
        all_btn  = QPushButton("All")
        none_btn = QPushButton("None")
        all_btn.clicked.connect(self._select_all)
        none_btn.clicked.connect(self._select_none)
        btn_row.addWidget(all_btn)
        btn_row.addWidget(none_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _parse_cpulist(self, cpulist: str) -> set[int]:
        result = set()
        if not cpulist:
            return set(range(self._cpu_count))
        for part in cpulist.split(","):
            part = part.strip()
            if "-" in part:
                sub = part.split("-")
                try:
                    lo, hi = int(sub[0]), int(sub[1])
                    result.update(range(lo, hi + 1))
                except ValueError:
                    pass
            else:
                try:
                    result.add(int(part))
                except ValueError:
                    pass
        return result

    def _select_all(self):
        for cb in self._checkboxes:
            if cb.isEnabled():
                cb.setChecked(True)

    def _select_none(self):
        for cb in self._checkboxes:
            if cb.isEnabled():
                cb.setChecked(False)

    def _validate_and_accept(self):
        selected = [i for i, cb in enumerate(self._checkboxes) if cb.isChecked()]
        if not selected:
            QMessageBox.warning(self, "Invalid", "At least one CPU must be selected.")
            return
        self.accept()

    def get_cpulist(self) -> str:
        """Return compact cpulist string for selected CPUs."""
        selected = sorted(i for i, cb in enumerate(self._checkboxes) if cb.isChecked())
        if not selected:
            return ""
        ranges = []
        start = selected[0]
        end = selected[0]
        for c in selected[1:]:
            if c == end + 1:
                end = c
            else:
                ranges.append(f"{start}-{end}" if start != end else str(start))
                start = end = c
        ranges.append(f"{start}-{end}" if start != end else str(start))
        return ",".join(ranges)


class NicePriorityDialog(QDialog):
    """Nice priority picker (-20 to 19)."""

    def __init__(self, current_nice: int = 0, parent=None, title_suffix: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Set CPU Priority (nice){' — ' + title_suffix if title_suffix else ''}")
        self._build_ui(current_nice)

    def _build_ui(self, current_nice: int):
        layout = QVBoxLayout(self)

        info = QLabel(
            "Nice priority: lower = higher priority.\n"
            "Negative values require root (will fail silently if not root)."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        row = QHBoxLayout()
        row.addWidget(QLabel("Nice value:"))
        self._spin = QSpinBox()
        self._spin.setRange(-20, 19)
        self._spin.setValue(current_nice)
        row.addWidget(self._spin)
        row.addStretch()
        layout.addLayout(row)

        # Quick presets
        presets_row = QHBoxLayout()
        presets_row.addWidget(QLabel("Presets:"))
        for label, val in [("High (-10)", -10), ("Normal (0)", 0), ("Low (5)", 5), ("Very Low (15)", 15), ("Idle (19)", 19)]:
            btn = QPushButton(label)
            btn.setFixedWidth(110)
            btn.clicked.connect(lambda checked, v=val: self._spin.setValue(v))
            presets_row.addWidget(btn)
        presets_row.addStretch()
        layout.addLayout(presets_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_nice(self) -> int:
        return self._spin.value()


class IoNiceDialog(QDialog):
    """I/O priority class and level picker."""

    CLASSES = [
        (0, "None (default)"),
        (1, "Realtime (root)"),
        (2, "Best-effort"),
        (3, "Idle"),
    ]

    def __init__(self, current_class: int = 2, current_level: int = 4, parent=None, title_suffix: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Set I/O Priority{' — ' + title_suffix if title_suffix else ''}")
        self._build_ui(current_class, current_level)

    def _build_ui(self, current_class: int, current_level: int):
        layout = QVBoxLayout(self)

        info = QLabel("I/O class: Realtime requires root. Level 0=highest, 7=lowest (for RT and BE).")
        info.setWordWrap(True)
        layout.addWidget(info)

        grid = QGridLayout()
        grid.addWidget(QLabel("I/O Class:"), 0, 0)
        self._class_combo = QComboBox()
        for val, label in self.CLASSES:
            self._class_combo.addItem(label, val)
        idx = next((i for i, (v, _) in enumerate(self.CLASSES) if v == current_class), 2)
        self._class_combo.setCurrentIndex(idx)
        self._class_combo.currentIndexChanged.connect(self._on_class_changed)
        grid.addWidget(self._class_combo, 0, 1)

        grid.addWidget(QLabel("I/O Level (0-7):"), 1, 0)
        self._level_spin = QSpinBox()
        self._level_spin.setRange(0, 7)
        self._level_spin.setValue(current_level)
        grid.addWidget(self._level_spin, 1, 1)
        layout.addLayout(grid)

        self._on_class_changed()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_class_changed(self):
        cls = self._class_combo.currentData()
        self._level_spin.setEnabled(cls in (1, 2))

    def get_ionice_class(self) -> int:
        return self._class_combo.currentData()

    def get_ionice_level(self) -> int:
        return self._level_spin.value()


class ProcessPickerDialog(QDialog):
    """Live process list — pick one to auto-fill a rule pattern."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Running Process")
        self.setMinimumSize(560, 420)
        self._selected_name = ""
        self._selected_affinity = ""
        self._build_ui()
        # Populate after dialog is shown
        QTimer.singleShot(0, self._populate)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Filter:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Type to filter by name…")
        self._search.textChanged.connect(self._filter)
        search_row.addWidget(self._search)
        layout.addLayout(search_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["PID", "Name", "CPU%", "Affinity"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.doubleClicked.connect(self.accept)
        layout.addWidget(self._table)

        self._all_rows: list[tuple] = []  # (pid, name, cpu, affinity)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self):
        import psutil
        rows = []
        for proc in psutil.process_iter():
            try:
                with proc.oneshot():
                    pid = proc.pid
                    name = proc.name()
                    try:
                        cmdline = proc.cmdline()
                    except Exception:
                        cmdline = []
                    # Use same Wine name resolution as monitor
                    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
                    from monitor import _resolve_name
                    name = _resolve_name(name, cmdline)
                    cpu = proc.cpu_percent()
                    try:
                        aff = set(proc.cpu_affinity())
                        aff_str = utils._cpuset_to_cpulist(aff)
                    except Exception:
                        aff_str = ""
                    rows.append((pid, name, cpu, aff_str))
            except Exception:
                pass
        rows.sort(key=lambda r: r[2], reverse=True)  # sort by CPU% desc
        self._all_rows = rows
        self._render_rows(rows)

    def _render_rows(self, rows):
        self._table.setRowCount(len(rows))
        for r, (pid, name, cpu, aff) in enumerate(rows):
            self._table.setItem(r, 0, QTableWidgetItem(str(pid)))
            self._table.setItem(r, 1, QTableWidgetItem(name))
            item_cpu = QTableWidgetItem(f"{cpu:.1f}")
            item_cpu.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(r, 2, item_cpu)
            self._table.setItem(r, 3, QTableWidgetItem(aff))

    def _filter(self, text: str):
        text = text.lower()
        filtered = [r for r in self._all_rows if text in r[1].lower()]
        self._render_rows(filtered)

    def _on_accept(self):
        row = self._table.currentRow()
        if row < 0:
            return
        self._selected_name = self._table.item(row, 1).text() if self._table.item(row, 1) else ""
        self._selected_affinity = self._table.item(row, 3).text() if self._table.item(row, 3) else ""
        self.accept()

    def get_selected_name(self) -> str:
        return self._selected_name

    def get_selected_affinity(self) -> str:
        return self._selected_affinity


class RuleEditDialog(QDialog):
    """Add/Edit a rule dialog."""

    def __init__(self, rule=None, parent=None):
        super().__init__(parent)
        self._rule = rule
        self.setWindowTitle("Edit Rule" if rule else "Add Rule")
        self.setMinimumWidth(460)
        self._build_ui()

    def _build_ui(self):
        from PyQt6.QtWidgets import QFormLayout
        layout = QVBoxLayout(self)

        # Process picker button at top
        pick_row = QHBoxLayout()
        pick_btn = QPushButton("Select from running processes…")
        pick_btn.clicked.connect(self._pick_process)
        pick_row.addWidget(pick_btn)
        pick_row.addStretch()
        layout.addLayout(pick_row)

        form = QFormLayout()

        self._name_edit = QLineEdit()
        self._pattern_edit = QLineEdit()
        self._match_combo = QComboBox()
        for label in ["contains", "exact", "regex"]:
            self._match_combo.addItem(label)

        self._affinity_edit = QLineEdit()
        self._affinity_edit.setPlaceholderText("e.g. 0-3,5 (leave blank to skip)")

        self._nice_cb = QCheckBox("Enable")
        self._nice_spin = QSpinBox()
        self._nice_spin.setRange(-20, 19)
        self._nice_spin.setEnabled(False)
        self._nice_cb.toggled.connect(self._nice_spin.setEnabled)

        self._ionice_cb = QCheckBox("Enable")
        self._ionice_class_combo = QComboBox()
        for val, label in IoNiceDialog.CLASSES:
            self._ionice_class_combo.addItem(label, val)
        self._ionice_class_combo.setEnabled(False)
        self._ionice_level_spin = QSpinBox()
        self._ionice_level_spin.setRange(0, 7)
        self._ionice_level_spin.setEnabled(False)
        self._ionice_cb.toggled.connect(self._ionice_class_combo.setEnabled)
        self._ionice_cb.toggled.connect(self._ionice_level_spin.setEnabled)

        self._enabled_cb = QCheckBox("Rule enabled")
        self._enabled_cb.setChecked(True)

        form.addRow("Name:", self._name_edit)
        form.addRow("Pattern:", self._pattern_edit)
        form.addRow("Match type:", self._match_combo)
        form.addRow("CPU Affinity:", self._affinity_edit)

        nice_row = QHBoxLayout()
        nice_row.addWidget(self._nice_cb)
        nice_row.addWidget(self._nice_spin)
        nice_row.addStretch()
        form.addRow("Nice priority:", nice_row)

        ionice_row = QHBoxLayout()
        ionice_row.addWidget(self._ionice_cb)
        ionice_row.addWidget(self._ionice_class_combo)
        ionice_row.addWidget(QLabel("Lvl:"))
        ionice_row.addWidget(self._ionice_level_spin)
        ionice_row.addStretch()
        form.addRow("I/O priority:", ionice_row)

        form.addRow("", self._enabled_cb)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Populate if editing existing rule
        if self._rule:
            self._name_edit.setText(self._rule.name)
            self._pattern_edit.setText(self._rule.pattern)
            idx = self._match_combo.findText(self._rule.match_type)
            if idx >= 0:
                self._match_combo.setCurrentIndex(idx)
            if self._rule.affinity:
                self._affinity_edit.setText(self._rule.affinity)
            if self._rule.nice is not None:
                self._nice_cb.setChecked(True)
                self._nice_spin.setValue(self._rule.nice)
            if self._rule.ionice_class is not None:
                self._ionice_cb.setChecked(True)
                idx2 = next(
                    (i for i, (v, _) in enumerate(IoNiceDialog.CLASSES) if v == self._rule.ionice_class),
                    2
                )
                self._ionice_class_combo.setCurrentIndex(idx2)
                if self._rule.ionice_level is not None:
                    self._ionice_level_spin.setValue(self._rule.ionice_level)
            self._enabled_cb.setChecked(self._rule.enabled)

    def _pick_process(self):
        """Open live process picker and auto-fill name/pattern from selection."""
        dlg = ProcessPickerDialog(self)
        if dlg.exec() == ProcessPickerDialog.DialogCode.Accepted:
            name = dlg.get_selected_name()
            if name:
                if not self._name_edit.text():
                    self._name_edit.setText(name)
                self._pattern_edit.setText(name)
                # Default to exact match when picking from live list
                idx = self._match_combo.findText("exact")
                if idx >= 0:
                    self._match_combo.setCurrentIndex(idx)
                # Pre-fill affinity from the selected process if not already set
                if not self._affinity_edit.text():
                    self._affinity_edit.setText(dlg.get_selected_affinity())

    def _validate_and_accept(self):
        from PyQt6.QtWidgets import QMessageBox
        if not self._pattern_edit.text().strip():
            QMessageBox.warning(self, "Validation", "Pattern cannot be empty.")
            return
        affinity = self._affinity_edit.text().strip()
        if affinity and not utils.validate_cpulist(affinity):
            QMessageBox.warning(self, "Validation", f"Invalid CPU affinity format: '{affinity}'")
            return
        self.accept()

    def get_rule(self):
        """Return Rule built from dialog values."""
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from rules import Rule

        rule_id = self._rule.rule_id if self._rule else None
        affinity = self._affinity_edit.text().strip() or None
        nice = self._nice_spin.value() if self._nice_cb.isChecked() else None
        ionice_class = self._ionice_class_combo.currentData() if self._ionice_cb.isChecked() else None
        ionice_level = self._ionice_level_spin.value() if self._ionice_cb.isChecked() else None

        r = Rule(
            name=self._name_edit.text().strip(),
            pattern=self._pattern_edit.text().strip(),
            match_type=self._match_combo.currentText(),
            affinity=affinity,
            nice=nice,
            ionice_class=ionice_class,
            ionice_level=ionice_level,
            enabled=self._enabled_cb.isChecked(),
        )
        if rule_id:
            r.rule_id = rule_id
        return r
