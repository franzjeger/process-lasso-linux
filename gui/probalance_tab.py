"""ProBalance settings tab."""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QGroupBox,
    QDoubleSpinBox, QSpinBox, QCheckBox, QPushButton,
    QListWidget, QHBoxLayout, QLineEdit, QLabel, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal


class ProBalanceTab(QWidget):
    settings_changed = pyqtSignal(dict)  # emit updated probalance config dict

    def __init__(self, pb_config: dict, parent=None):
        super().__init__(parent)
        self._build_ui(pb_config)

    def _build_ui(self, cfg: dict):
        layout = QVBoxLayout(self)

        # Enable toggle
        self._enabled_cb = QCheckBox("ProBalance Enabled")
        self._enabled_cb.setChecked(cfg.get("enabled", True))
        layout.addWidget(self._enabled_cb)

        # Throttle group
        throttle_group = QGroupBox("Throttle Settings")
        form = QFormLayout(throttle_group)

        self._cpu_thresh = QDoubleSpinBox()
        self._cpu_thresh.setRange(10.0, 100.0)
        self._cpu_thresh.setSingleStep(5.0)
        self._cpu_thresh.setSuffix(" %")
        self._cpu_thresh.setValue(cfg.get("cpu_threshold_percent", 85.0))
        form.addRow("CPU threshold:", self._cpu_thresh)

        self._consec_secs = QSpinBox()
        self._consec_secs.setRange(1, 60)
        self._consec_secs.setSuffix(" s")
        self._consec_secs.setValue(cfg.get("consecutive_seconds", 3))
        form.addRow("Consecutive seconds above threshold:", self._consec_secs)

        self._nice_adj = QSpinBox()
        self._nice_adj.setRange(1, 19)
        self._nice_adj.setValue(cfg.get("nice_adjustment", 10))
        form.addRow("Nice adjustment (added on throttle):", self._nice_adj)

        self._nice_floor = QSpinBox()
        self._nice_floor.setRange(1, 19)
        self._nice_floor.setValue(cfg.get("nice_floor", 15))
        form.addRow("Nice floor (max nice applied):", self._nice_floor)

        layout.addWidget(throttle_group)

        # Restore group
        restore_group = QGroupBox("Restore Settings")
        form2 = QFormLayout(restore_group)

        self._restore_thresh = QDoubleSpinBox()
        self._restore_thresh.setRange(1.0, 99.0)
        self._restore_thresh.setSingleStep(5.0)
        self._restore_thresh.setSuffix(" %")
        self._restore_thresh.setValue(cfg.get("restore_threshold_percent", 40.0))
        form2.addRow("Restore when CPU below:", self._restore_thresh)

        self._restore_hyst = QSpinBox()
        self._restore_hyst.setRange(1, 120)
        self._restore_hyst.setSuffix(" s")
        self._restore_hyst.setValue(cfg.get("restore_hysteresis_seconds", 5))
        form2.addRow("Restore hysteresis (seconds below restore threshold):", self._restore_hyst)

        layout.addWidget(restore_group)

        # Exempt patterns
        exempt_group = QGroupBox("Exempt Processes (pattern contains)")
        ex_layout = QVBoxLayout(exempt_group)

        self._exempt_list = QListWidget()
        for pat in cfg.get("exempt_patterns", []):
            self._exempt_list.addItem(pat)
        ex_layout.addWidget(self._exempt_list)

        ex_edit_row = QHBoxLayout()
        self._exempt_edit = QLineEdit()
        self._exempt_edit.setPlaceholderText("Pattern to exempt...")
        add_ex_btn = QPushButton("Add")
        del_ex_btn = QPushButton("Remove selected")
        add_ex_btn.clicked.connect(self._add_exempt)
        del_ex_btn.clicked.connect(self._del_exempt)
        ex_edit_row.addWidget(self._exempt_edit)
        ex_edit_row.addWidget(add_ex_btn)
        ex_edit_row.addWidget(del_ex_btn)
        ex_layout.addLayout(ex_edit_row)

        layout.addWidget(exempt_group)

        # Apply button
        apply_btn = QPushButton("Apply Settings")
        apply_btn.clicked.connect(self._apply)
        layout.addWidget(apply_btn)
        layout.addStretch()

    def _add_exempt(self):
        text = self._exempt_edit.text().strip()
        if text:
            self._exempt_list.addItem(text)
            self._exempt_edit.clear()

    def _del_exempt(self):
        for item in self._exempt_list.selectedItems():
            self._exempt_list.takeItem(self._exempt_list.row(item))

    def _apply(self):
        cfg = self.get_config()
        self.settings_changed.emit(cfg)
        QMessageBox.information(self, "ProBalance", "Settings applied.")

    def get_config(self) -> dict:
        exempt = [
            self._exempt_list.item(i).text()
            for i in range(self._exempt_list.count())
        ]
        return {
            "enabled": self._enabled_cb.isChecked(),
            "cpu_threshold_percent": self._cpu_thresh.value(),
            "consecutive_seconds": self._consec_secs.value(),
            "nice_adjustment": self._nice_adj.value(),
            "nice_floor": self._nice_floor.value(),
            "restore_threshold_percent": self._restore_thresh.value(),
            "restore_hysteresis_seconds": self._restore_hyst.value(),
            "exempt_patterns": exempt,
        }
