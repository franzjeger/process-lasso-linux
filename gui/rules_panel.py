"""Rules panel: list of saved rules with add/edit/delete/toggle."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QMessageBox, QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal
import json

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
        add_btn      = QPushButton("Add Rule")
        templates_btn = QPushButton("Templates…")
        edit_btn     = QPushButton("Edit")
        del_btn      = QPushButton("Delete")
        toggle_btn   = QPushButton("Enable/Disable")
        export_btn   = QPushButton("Export…")
        import_btn   = QPushButton("Import…")
        add_btn.clicked.connect(self._add_rule)
        templates_btn.clicked.connect(self._show_presets)
        edit_btn.clicked.connect(self._edit_selected)
        del_btn.clicked.connect(self._delete_selected)
        toggle_btn.clicked.connect(self._toggle_selected)
        export_btn.clicked.connect(self._export_rules)
        import_btn.clicked.connect(self._import_rules)
        for b in [add_btn, templates_btn, edit_btn, del_btn, toggle_btn, export_btn, import_btn]:
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

    def _show_presets(self):
        from gui.dialogs import RulePresetsDialog, RuleEditDialog
        pdlg = RulePresetsDialog(self)
        if pdlg.exec() != RulePresetsDialog.DialogCode.Accepted:
            return
        preset = pdlg.get_preset()
        if not preset:
            return
        name, pat, match, aff, nice, ioc, iol = preset
        template = Rule(
            name=name, pattern=pat, match_type=match,
            affinity=aff, nice=nice,
            ionice_class=ioc, ionice_level=iol,
        )
        dlg = RuleEditDialog(rule=template, parent=self)
        if dlg.exec() == RuleEditDialog.DialogCode.Accepted:
            rule = dlg.get_rule()
            self._engine.add_rule(rule)
            self.refresh()
            self.rules_changed.emit()

    def _export_rules(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Rules", "process_lasso_rules.json", "JSON files (*.json)"
        )
        if not path:
            return
        rules_data = [r.to_dict() for r in self._engine.get_rules()]
        try:
            with open(path, "w") as f:
                json.dump(rules_data, f, indent=2)
            QMessageBox.information(self, "Export", f"Exported {len(rules_data)} rules to {path}")
        except OSError as e:
            QMessageBox.warning(self, "Export Failed", str(e))

    def _import_rules(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Rules", "", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            imported = 0
            for d in data:
                try:
                    rule = Rule.from_dict(d)
                    self._engine.add_rule(rule)
                    imported += 1
                except Exception:
                    pass
            self.refresh()
            self.rules_changed.emit()
            QMessageBox.information(self, "Import", f"Imported {imported} rules from {path}")
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.warning(self, "Import Failed", str(e))
