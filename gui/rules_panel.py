"""Rules panel: list of saved rules with add/edit/delete/toggle."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal

from rules import RuleEngine, Rule
from gui.dialogs import RuleEditDialog


class RulesPanel(QWidget):
    rules_changed = pyqtSignal()  # emit when rules list changes

    def __init__(self, rule_engine: RuleEngine, parent=None):
        super().__init__(parent)
        self._engine = rule_engine
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        COLS = ["Enabled", "Name", "Pattern", "Match", "Affinity", "Nice", "I/O Class", "I/O Lvl"]
        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.doubleClicked.connect(self._edit_selected)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Rule")
        edit_btn = QPushButton("Edit")
        del_btn = QPushButton("Delete")
        toggle_btn = QPushButton("Enable/Disable")
        add_btn.clicked.connect(self._add_rule)
        edit_btn.clicked.connect(self._edit_selected)
        del_btn.clicked.connect(self._delete_selected)
        toggle_btn.clicked.connect(self._toggle_selected)
        for b in [add_btn, edit_btn, del_btn, toggle_btn]:
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def refresh(self):
        rules = self._engine.get_rules()
        self._table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            items = [
                "Yes" if rule.enabled else "No",
                rule.name,
                rule.pattern,
                rule.match_type,
                rule.affinity or "",
                str(rule.nice) if rule.nice is not None else "",
                str(rule.ionice_class) if rule.ionice_class is not None else "",
                str(rule.ionice_level) if rule.ionice_level is not None else "",
            ]
            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, rule.rule_id)
                self._table.setItem(row, col, item)

    def _selected_rule_id(self) -> str | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _add_rule(self):
        dlg = RuleEditDialog(parent=self)
        if dlg.exec() == RuleEditDialog.DialogCode.Accepted:
            rule = dlg.get_rule()
            self._engine.add_rule(rule)
            self.refresh()
            self.rules_changed.emit()

    def add_rule_direct(self, rule: Rule):
        """Called from ProcessTable 'Add Rule' context menu."""
        self._engine.add_rule(rule)
        self.refresh()
        self.rules_changed.emit()

    def _edit_selected(self):
        rule_id = self._selected_rule_id()
        if not rule_id:
            return
        rule = next((r for r in self._engine.get_rules() if r.rule_id == rule_id), None)
        if not rule:
            return
        dlg = RuleEditDialog(rule=rule, parent=self)
        if dlg.exec() == RuleEditDialog.DialogCode.Accepted:
            updated = dlg.get_rule()
            self._engine.update_rule(updated)
            self.refresh()
            self.rules_changed.emit()

    def _delete_selected(self):
        rule_id = self._selected_rule_id()
        if not rule_id:
            return
        rule = next((r for r in self._engine.get_rules() if r.rule_id == rule_id), None)
        if not rule:
            return
        ans = QMessageBox.question(
            self, "Delete Rule",
            f"Delete rule '{rule.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._engine.remove_rule(rule_id)
            self.refresh()
            self.rules_changed.emit()

    def _toggle_selected(self):
        rule_id = self._selected_rule_id()
        if not rule_id:
            return
        rule = next((r for r in self._engine.get_rules() if r.rule_id == rule_id), None)
        if not rule:
            return
        rule.enabled = not rule.enabled
        self._engine.update_rule(rule)
        self.refresh()
        self.rules_changed.emit()
