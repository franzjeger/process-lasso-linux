"""htop-style per-CPU utilization bar widget."""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSlot
from PyQt6.QtGui import QPainter, QColor, QFont, QPen


# Bar background / border — glass purple
_BG     = QColor(10, 4, 22, 180)      # dark purple glass
_BORDER = QColor(109, 40, 217, 70)    # subtle purple glow border
_ONLINE = QColor(196, 181, 253)        # light lavender — CPU index text
_OFFLIN = QColor(76, 29, 149, 130)    # muted purple — offline label

# Load colour ramp: green → yellow → orange → red (smooth interpolation)
_RAMP = [
    (0,   34,  197,  94),   # #22c55e  green
    (25,  163, 230,  53),   # #a3e635  yellow-green
    (50,  234, 179,   8),   # #eab308  yellow
    (70,  249, 115,  22),   # #f97316  orange
    (85,  239,  68,  68),   # #ef4444  red
    (100, 220,  38,  38),   # #dc2626  deep red
]


def _bar_color(pct: float) -> QColor:
    """Smooth interpolated colour across the load ramp."""
    pct = max(0.0, min(100.0, pct))
    for i in range(len(_RAMP) - 1):
        p0, r0, g0, b0 = _RAMP[i]
        p1, r1, g1, b1 = _RAMP[i + 1]
        if pct <= p1:
            t = (pct - p0) / (p1 - p0) if p1 > p0 else 0.0
            return QColor(
                int(r0 + t * (r1 - r0)),
                int(g0 + t * (g1 - g0)),
                int(b0 + t * (b1 - b0)),
            )
    r, g, b = _RAMP[-1][1], _RAMP[-1][2], _RAMP[-1][3]
    return QColor(r, g, b)


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

            # Background (glass dark purple)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_BG)
            p.drawRoundedRect(x, y, bar_w, bar_h, 4, 4)

            # Filled portion (skip for offline CPUs)
            if not offline and pct > 0:
                fill_w = max(0, int((bar_w - label_w - 2) * pct / 100))
                p.setBrush(_bar_color(pct))
                p.drawRoundedRect(x + label_w + 1, y + 2,
                                  fill_w, bar_h - 4, 2, 2)

            # Border (purple glow)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_BORDER, 1))
            p.drawRoundedRect(x, y, bar_w, bar_h, 4, 4)

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
                p.setPen(QColor(255, 255, 255, 220) if pct >= 50 else _ONLINE)
            p.drawText(x + label_w + 2, y, bar_w - label_w - 4, bar_h,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       pct_text)

        p.end()
