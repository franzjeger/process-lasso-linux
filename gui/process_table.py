"""Process table widget with live data and right-click context menu."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QAbstractItemView,
    QMenu, QHeaderView, QMessageBox, QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence

import utils
from gui.dialogs import AffinityDialog, NicePriorityDialog, IoNiceDialog, RuleEditDialog


class ProcessTable(QTableWidget):
    """Sortable process table with right-click context menu."""

    rule_add_requested = pyqtSignal(object)  # emits Rule
    affinity_manually_changed = pyqtSignal(int)  # pid — suppress rule re-enforcement

    COLUMNS = ["PID", "Name", "CPU%", "Mem(MB)", "Nice", "Affinity", "I/O", "Status"]

    def __init__(self, rule_engine, log_callback, parent=None):
        super().__init__(0, len(self.COLUMNS), parent)
        self._rule_engine = rule_engine
        self._log_callback = log_callback
        self._snapshot: list[dict] = []
        self._throttled_pids: set[int] = set()
        self._sort_col = 2   # CPU%
        self._sort_asc = False
        self._filter_text: str = ""
        self._col_visible: list[bool] = [True] * len(self.COLUMNS)
        self._setup()

    def _setup(self):
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionsClickable(True)
        hdr.sectionClicked.connect(self._on_header_click)
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._show_header_menu)
        self.setSortingEnabled(False)  # Manual sorting

    def _show_header_menu(self, pos):
        """Right-click on header — toggle column visibility."""
        menu = QMenu(self)
        hdr = self.horizontalHeader()
        for i, name in enumerate(self.COLUMNS):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(not hdr.isSectionHidden(i))
            action.setData(i)
        chosen = menu.exec(hdr.mapToGlobal(pos))
        if chosen:
            col = chosen.data()
            hidden = hdr.isSectionHidden(col)
            hdr.setSectionHidden(col, not hidden)

    def _update_header_labels(self):
        """Re-set header labels (called after sort to add/remove arrow indicators)."""
        labels = []
        for i, name in enumerate(self.COLUMNS):
            if i == self._sort_col:
                labels.append(f"{name} {'▲' if self._sort_asc else '▼'}")
            else:
                labels.append(name)
        self.setHorizontalHeaderLabels(labels)

    def _on_header_click(self, col: int):
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = col not in (2, 3)  # CPU/Mem default desc
        self._update_header_labels()
        self._refresh_display()

    def update_throttled(self, throttled_pids: set[int]):
        self._throttled_pids = throttled_pids

    def update_snapshot(self, snapshot: list[dict]):
        self._snapshot = snapshot
        self._refresh_display()

    def set_filter(self, text: str):
        """Filter displayed processes by name or PID (case-insensitive substring)."""
        self._filter_text = text.strip().lower()
        self._refresh_display()

    def _refresh_display(self):
        key_map = {
            0: lambda p: p["pid"],
            1: lambda p: p["name"].lower(),
            2: lambda p: p["cpu_percent"],
            3: lambda p: p["mem_rss"],
            4: lambda p: p["nice"],
            5: lambda p: p["affinity"],
            6: lambda p: p["ionice"],
            7: lambda p: "",
        }
        key_fn = key_map.get(self._sort_col, lambda p: 0)
        try:
            sorted_snap = sorted(self._snapshot, key=key_fn, reverse=not self._sort_asc)
        except Exception:
            sorted_snap = self._snapshot

        # Apply filter
        if self._filter_text:
            ft = self._filter_text
            sorted_snap = [
                p for p in sorted_snap
                if ft in p["name"].lower() or ft in str(p["pid"])
            ]

        self.setRowCount(len(sorted_snap))
        for row, proc in enumerate(sorted_snap):
            pid = proc["pid"]
            cpu = proc["cpu_percent"]
            throttled = pid in self._throttled_pids
            items = [
                str(pid),
                proc["name"],
                f"{cpu:.1f}",
                f"{proc['mem_rss'] / 1_048_576:.1f}",
                str(proc["nice"]),
                proc.get("affinity", ""),
                proc.get("ionice", ""),
                "⏸ Throttled" if throttled else "",
            ]
            # Pick row text color based on CPU usage or throttle state
            if throttled:
                row_color = QColor("#fab387")   # orange
            elif cpu >= 80:
                row_color = QColor("#f38ba8")   # red
            elif cpu >= 40:
                row_color = QColor("#f9e2af")   # yellow
            elif cpu >= 10:
                row_color = QColor("#a6e3a1")   # green
            else:
                row_color = None                # default text color

            cmdline = proc.get("cmdline", "")
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                if col in (2, 3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
                if col == 1 and cmdline:
                    item.setToolTip(cmdline)
                if row_color is not None:
                    item.setForeground(row_color)
                self.setItem(row, col, item)

    def _selected_proc(self) -> dict | None:
        rows = self.selectedItems()
        if not rows:
            return None
        row = self.currentRow()
        pid_item = self.item(row, 0)
        name_item = self.item(row, 1)
        nice_item = self.item(row, 4)
        affinity_item = self.item(row, 5)
        ionice_item = self.item(row, 6)
        if not pid_item:
            return None
        return {
            "pid": int(pid_item.text()),
            "name": name_item.text() if name_item else "",
            "nice": int(nice_item.text()) if nice_item else 0,
            "affinity": affinity_item.text() if affinity_item else "",
            "ionice": ionice_item.text() if ionice_item else "",
        }

    def _selected_procs(self) -> list[dict]:
        """Return all selected rows as proc dicts."""
        selected_rows = sorted({idx.row() for idx in self.selectedIndexes()})
        procs = []
        for row in selected_rows:
            pid_item = self.item(row, 0)
            name_item = self.item(row, 1)
            if not pid_item:
                continue
            procs.append({
                "pid": int(pid_item.text()),
                "name": name_item.text() if name_item else "",
            })
        return procs

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            procs = self._selected_procs()
            if procs:
                self._do_kill_many(procs, force=False)
                return
        super().keyPressEvent(event)

    def _show_context_menu(self, pos):
        proc = self._selected_proc()
        procs = self._selected_procs()
        if not proc:
            return
        menu = QMenu(self)
        if len(procs) > 1:
            menu.addAction(
                f"Kill {len(procs)} selected processes",
                lambda: self._do_kill_many(procs, force=False)
            )
            menu.addAction(
                f"Force Kill {len(procs)} selected processes",
                lambda: self._do_kill_many(procs, force=True)
            )
            menu.addSeparator()
        else:
            menu.addAction(
                f"Kill {proc['name']} ({proc['pid']})",
                lambda: self._do_kill(proc, force=False)
            )
            menu.addAction(
                f"Force Kill {proc['name']} ({proc['pid']})",
                lambda: self._do_kill(proc, force=True)
            )
            menu.addSeparator()
        menu.addAction(
            f"Set Affinity for {proc['name']} ({proc['pid']})",
            lambda: self._do_set_affinity(proc)
        )
        menu.addAction(
            f"Set Priority (nice) for {proc['name']} ({proc['pid']})",
            lambda: self._do_set_nice(proc)
        )
        menu.addAction(
            f"Set I/O Priority for {proc['name']} ({proc['pid']})",
            lambda: self._do_set_ionice(proc)
        )
        menu.addSeparator()
        menu.addAction(
            f"Add Rule for '{proc['name']}'...",
            lambda: self._do_add_rule(proc)
        )
        menu.exec(self.viewport().mapToGlobal(pos))

    def _do_kill(self, proc: dict, force: bool):
        import signal
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            import os
            os.kill(proc["pid"], sig)
            msg = f"{'Force k' if force else 'K'}illed {proc['name']} ({proc['pid']})"
        except OSError as e:
            msg = f"Kill failed for {proc['name']} ({proc['pid']}): {e}"
        if self._log_callback:
            self._log_callback(msg)

    def _do_kill_many(self, procs: list[dict], force: bool):
        if not procs:
            return
        names = ", ".join(f"{p['name']}({p['pid']})" for p in procs[:4])
        if len(procs) > 4:
            names += f" and {len(procs) - 4} more"
        ans = QMessageBox.question(
            self, "Confirm Kill",
            f"{'Force kill' if force else 'Kill'} {len(procs)} processes?\n{names}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        for p in procs:
            self._do_kill(p, force)

    def _do_set_affinity(self, proc: dict):
        dlg = AffinityDialog(proc.get("affinity", ""), self, proc["name"])
        if dlg.exec() == AffinityDialog.DialogCode.Accepted:
            cpulist = dlg.get_cpulist()
            if utils.set_affinity(proc["pid"], cpulist):
                msg = f"Set affinity={cpulist} on {proc['name']}({proc['pid']})"
                # Tell the monitor not to override this for 30 s
                self.affinity_manually_changed.emit(proc["pid"])
            else:
                msg = f"Failed to set affinity on {proc['name']}({proc['pid']})"
            if self._log_callback:
                self._log_callback(msg)

    def _do_set_nice(self, proc: dict):
        dlg = NicePriorityDialog(proc.get("nice", 0), self, proc["name"])
        if dlg.exec() == NicePriorityDialog.DialogCode.Accepted:
            nice = dlg.get_nice()
            if utils.set_nice(proc["pid"], nice):
                msg = f"Set nice={nice} on {proc['name']}({proc['pid']})"
            else:
                msg = f"Failed to set nice={nice} on {proc['name']}({proc['pid']}) (root needed?)"
            if self._log_callback:
                self._log_callback(msg)

    def _do_set_ionice(self, proc: dict):
        dlg = IoNiceDialog(parent=self, title_suffix=proc["name"])
        if dlg.exec() == IoNiceDialog.DialogCode.Accepted:
            cls = dlg.get_ionice_class()
            lvl = dlg.get_ionice_level()
            if utils.set_ionice(proc["pid"], cls, lvl):
                msg = f"Set ionice class={cls} level={lvl} on {proc['name']}({proc['pid']})"
            else:
                msg = f"Failed to set ionice on {proc['name']}({proc['pid']})"
            if self._log_callback:
                self._log_callback(msg)

    def _do_add_rule(self, proc: dict):
        from rules import Rule
        # Pre-populate with process name
        template = Rule(name=proc["name"], pattern=proc["name"], match_type="contains")
        dlg = RuleEditDialog(rule=template, parent=self)
        if dlg.exec() == RuleEditDialog.DialogCode.Accepted:
            rule = dlg.get_rule()
            self.rule_add_requested.emit(rule)
