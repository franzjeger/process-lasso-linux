"""htop-style per-CPU utilization bar widget."""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QPainter, QColor, QFont, QPen


# Colour stops (Catppuccin Mocha palette)
_LOW    = QColor("#a6e3a1")   # green   — 0–30%
_MED    = QColor("#f9e2af")   # yellow  — 30–60%
_HIGH   = QColor("#fab387")   # orange  — 60–80%
_CRIT   = QColor("#f38ba8")   # red     — 80–100%
_BG     = QColor("#181825")   # bar background
_BORDER = QColor("#313244")   # bar border
_TEXT   = QColor("#585b70")   # label text (CPU number)
_ONLINE = QColor("#a6adc8")   # CPU index text (online)
_OFFLIN = QColor("#45475a")   # CPU index text (offline / parked)


def _bar_color(pct: float) -> QColor:
    if pct >= 80:
        return _CRIT
    if pct >= 60:
        return _HIGH
    if pct >= 30:
        return _MED
    return _LOW


class CpuBarsWidget(QWidget):
    """Compact grid of per-CPU horizontal utilization bars.

    Automatically lays bars out in rows to fit the available width.
    Each bar shows: CPU index label | filled portion | % text.
    Parked (offline) CPUs are shown greyed out.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cpu_pcts: list[float] = []
        self._offline: set[int] = set()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(30)

    @pyqtSlot(list)
    def update_cpu(self, percpu: list[float]):
        self._cpu_pcts = list(percpu)
        # Refresh offline set from sysfs (fast — just reads a small file)
        try:
            import cpu_park
            self._offline = cpu_park.get_offline_cpus()
        except Exception:
            self._offline = set()
        self.update()  # triggers paintEvent
        # Resize height to fit the number of rows needed
        n = len(self._cpu_pcts)
        if n == 0:
            return
        bar_h = 20
        gap   = 3
        cols  = self._cols(n)
        rows  = (n + cols - 1) // cols
        needed = rows * (bar_h + gap) + gap + 4
        self.setMinimumHeight(needed)
        self.setMaximumHeight(needed)

    def _cols(self, n: int) -> int:
        """Choose number of columns that fill width reasonably."""
        w = self.width() or 900
        bar_min_w = 90    # minimum bar width including label
        cols = max(1, w // bar_min_w)
        return min(cols, n)

    def paintEvent(self, event):
        n = len(self._cpu_pcts)
        if n == 0:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w     = self.width()
        bar_h = 20
        gap   = 3
        cols  = self._cols(n)
        bar_w = max(60, (w - gap * (cols + 1)) // cols)

        label_w = 30   # fixed width for "CPU N" label on the left
        font = QFont()
        font.setPixelSize(10)
        font.setFamily("monospace")
        p.setFont(font)

        for i, pct in enumerate(self._cpu_pcts):
            col  = i % cols
            row  = i // cols
            x    = gap + col * (bar_w + gap)
            y    = gap + row * (bar_h + gap)

            offline = i in self._offline

            # Background
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_BG)
            p.drawRoundedRect(x, y, bar_w, bar_h, 3, 3)

            # Filled portion (skip for offline CPUs)
            if not offline and pct > 0:
                fill_w = max(0, int((bar_w - label_w - 2) * pct / 100))
                p.setBrush(_bar_color(pct))
                p.drawRoundedRect(x + label_w + 1, y + 2,
                                  fill_w, bar_h - 4, 2, 2)

            # Border
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_BORDER, 1))
            p.drawRoundedRect(x, y, bar_w, bar_h, 3, 3)

            # CPU index label
            p.setPen(_OFFLIN if offline else _ONLINE)
            p.drawText(x + 2, y, label_w - 2, bar_h,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       str(i))

            # Percentage text (right-aligned inside bar)
            if offline:
                pct_text = "off"
                p.setPen(_OFFLIN)
            else:
                pct_text = f"{pct:.0f}%"
                p.setPen(Qt.GlobalColor.white if pct >= 60 else _ONLINE)
            p.drawText(x + label_w + 2, y, bar_w - label_w - 4, bar_h,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       pct_text)

        p.end()
