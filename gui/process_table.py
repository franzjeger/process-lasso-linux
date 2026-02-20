"""Process table widget with live data and right-click context menu."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import (
    QTableWidget, QTableWidgetItem, QAbstractItemView,
    QMenu, QHeaderView,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

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
        self._setup()

    def _setup(self):
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionsClickable(True)
        hdr.sectionClicked.connect(self._on_header_click)
        self.setSortingEnabled(False)  # Manual sorting

    def _on_header_click(self, col: int):
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = col not in (2, 3)  # CPU/Mem default desc
        self._refresh_display()

    def update_throttled(self, throttled_pids: set[int]):
        self._throttled_pids = throttled_pids

    def update_snapshot(self, snapshot: list[dict]):
        self._snapshot = snapshot
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
                row_color = QColor("#fab387")   # Catppuccin orange
            elif cpu >= 80:
                row_color = QColor("#f38ba8")   # Catppuccin red
            elif cpu >= 40:
                row_color = QColor("#f9e2af")   # Catppuccin yellow
            elif cpu >= 10:
                row_color = QColor("#a6e3a1")   # Catppuccin green
            else:
                row_color = None                # default text color

            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                if col in (2, 3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
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

    def _show_context_menu(self, pos):
        proc = self._selected_proc()
        if not proc:
            return
        menu = QMenu(self)
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
