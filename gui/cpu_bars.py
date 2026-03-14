"""htop-style per-CPU utilization bar widget."""
from __future__ import annotations

import os
from PyQt6.QtWidgets import QWidget, QSizePolicy, QToolTip
from PyQt6.QtCore import Qt, pyqtSlot, QPoint
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QLinearGradient


# Bar background / border — Breeze Dark
_BG     = QColor(27, 30, 32, 210)       # #1b1e20 dark background
_BORDER = QColor(59, 64, 69, 180)        # #3b4045 border
_ONLINE = QColor(239, 240, 241)          # #eff0f1 text
_OFFLIN = QColor(100, 110, 120, 150)     # muted — offline label

# Load colour ramp: green → yellow → orange → red (smooth interpolation)
_RAMP = [
    (0,   34,  197,  94),   # #22c55e  green
    (25,  163, 230,  53),   # #a3e635  yellow-green
    (50,  234, 179,   8),   # #eab308  yellow
    (70,  249, 115,  22),   # #f97316  orange
    (85,  239,  68,  68),   # #ef4444  red
    (100, 220,  38,  38),   # #dc2626  deep red
]

# Temperature warm tint colour (orange)
_TEMP_TINT = QColor(249, 115, 22)   # #f97316


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


def _read_cpu_freqs(n_cpus: int) -> dict[int, float]:
    """Read current CPU frequencies in GHz from sysfs scaling_cur_freq."""
    freqs: dict[int, float] = {}
    for cpu in range(n_cpus):
        try:
            path = f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq"
            khz = int(open(path).read().strip())
            freqs[cpu] = khz / 1_000_000.0   # kHz → GHz
        except (OSError, ValueError):
            pass
    return freqs


def _read_temps_all(n_cpus: int) -> dict[int, float]:
    """Read per-CPU temperatures from hwmon and /sys topology.

    Returns a dict mapping cpu_index → temperature_celsius.
    Reads from /sys/class/hwmon/hwmon*/ and uses
    /sys/devices/system/cpu/cpu{n}/topology/core_id to map core → CPUs.
    """
    temps: dict[int, float] = {}
    try:
        hwmon_base = "/sys/class/hwmon"
        for hwmon in os.listdir(hwmon_base):
            hwmon_path = os.path.join(hwmon_base, hwmon)
            # Check for CPU temperature labels
            try:
                name_file = os.path.join(hwmon_path, "name")
                hwmon_name = open(name_file).read().strip()
            except OSError:
                continue
            if hwmon_name not in ("coretemp", "k10temp", "zenpower"):
                continue
            # Read all tempN_input files
            for fname in os.listdir(hwmon_path):
                if not fname.startswith("temp") or not fname.endswith("_input"):
                    continue
                label_file = os.path.join(hwmon_path, fname.replace("_input", "_label"))
                input_file = os.path.join(hwmon_path, fname)
                try:
                    label = open(label_file).read().strip()
                    temp_millic = int(open(input_file).read().strip())
                    temp_c = temp_millic / 1000.0
                except (OSError, ValueError):
                    continue
                # Parse "Core N" labels
                if label.startswith("Core "):
                    try:
                        core_id = int(label.split()[1])
                    except (IndexError, ValueError):
                        continue
                    # Map core_id to CPUs
                    for cpu in range(n_cpus):
                        try:
                            cid = int(open(
                                f"/sys/devices/system/cpu/cpu{cpu}/topology/core_id"
                            ).read().strip())
                            if cid == core_id:
                                temps[cpu] = temp_c
                        except (OSError, ValueError):
                            pass
    except OSError:
        pass
    return temps


class CpuBarsWidget(QWidget):
    """Compact grid of per-CPU horizontal utilization bars.

    Automatically lays bars out in rows to fit the available width.
    Each bar shows: CPU index label | filled portion | % text.
    Parked (offline) CPUs are shown greyed out.
    Hover shows tooltip with CPU index and temperature.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cpu_pcts: list[float] = []
        self._offline: set[int] = set()
        self._temps: dict[int, float] = {}
        self._freqs: dict[int, float] = {}
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(30)
        self.setMouseTracking(True)

    @pyqtSlot(list)
    def update_cpu(self, percpu: list[float]):
        self._cpu_pcts = list(percpu)
        # Refresh offline set from sysfs (fast — just reads a small file)
        try:
            import cpu_park
            self._offline = cpu_park.get_offline_cpus()
        except Exception:
            self._offline = set()
        # Read temperatures and frequencies
        try:
            self._temps = _read_temps_all(len(self._cpu_pcts))
        except Exception:
            self._temps = {}
        try:
            self._freqs = _read_cpu_freqs(len(self._cpu_pcts))
        except Exception:
            self._freqs = {}
        self.update()  # triggers paintEvent
        # Resize height to fit the number of rows needed
        n = len(self._cpu_pcts)
        if n == 0:
            return
        bar_h = 26
        gap   = 3
        cols  = self._cols(n)
        rows  = (n + cols - 1) // cols
        needed = rows * (bar_h + gap) + gap + 4
        self.setMinimumHeight(needed)
        self.setMaximumHeight(needed)

    def _cols(self, n: int) -> int:
        """Choose number of columns that fill width reasonably with minimal wasted slots."""
        w = self.width() or 900
        bar_min_w = 90
        max_cols = min(max(1, w // bar_min_w), n)
        lo = max(1, max_cols // 2)

        # First pass: find largest column count (≤ max_cols, ≥ lo) that divides n evenly
        for c in range(max_cols, lo - 1, -1):
            if n % c == 0:
                return c

        # Second pass: no perfect divisor found — minimize wasted slots
        best_c = max_cols
        best_waste = (max_cols - n % max_cols) % max_cols
        for c in range(max_cols - 1, lo - 1, -1):
            waste = (c - n % c) % c
            if waste < best_waste:
                best_waste = waste
                best_c = c
        return best_c

    def _bar_index_at(self, pos: QPoint) -> int:
        """Return the CPU bar index under the given widget position, or -1."""
        n = len(self._cpu_pcts)
        if n == 0:
            return -1
        bar_h = 26
        gap   = 3
        cols  = self._cols(n)
        w     = self.width()
        bar_w = max(60, (w - gap * (cols + 1)) // cols)
        for i in range(n):
            col = i % cols
            row = i // cols
            x   = gap + col * (bar_w + gap)
            y   = gap + row * (bar_h + gap)
            if x <= pos.x() <= x + bar_w and y <= pos.y() <= y + bar_h:
                return i
        return -1

    def _temp_tint(self, temp: float) -> QColor:
        """Return a temperature tint colour blended with the bar colour.

        Returns orange tint colour scaled to temperature (40°C→80°C range).
        """
        if temp <= 40.0:
            return QColor(0, 0, 0, 0)
        t = min(1.0, (temp - 40.0) / 40.0)
        alpha = int(t * 120)
        return QColor(249, 115, 22, alpha)

    def event(self, ev) -> bool:
        """Handle ToolTip events to show per-CPU hover info."""
        from PyQt6.QtCore import QEvent
        if ev.type() == QEvent.Type.ToolTip:
            idx = self._bar_index_at(ev.pos())
            if idx >= 0 and idx < len(self._cpu_pcts):
                pct = self._cpu_pcts[idx]
                offline = idx in self._offline
                temp = self._temps.get(idx)
                if offline:
                    tip = f"CPU {idx}: offline (parked)"
                else:
                    tip = f"CPU {idx}: {pct:.1f}%"
                    freq = self._freqs.get(idx)
                    if freq is not None:
                        tip += f"  |  {freq:.2f} GHz"
                    if temp is not None:
                        tip += f"  |  {temp:.0f}°C"
                QToolTip.showText(ev.globalPos(), tip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(ev)

    def paintEvent(self, event):
        n = len(self._cpu_pcts)
        if n == 0:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w     = self.width()
        bar_h = 26
        gap   = 3
        cols  = self._cols(n)
        bar_w = max(60, (w - gap * (cols + 1)) // cols)

        label_w = 30   # fixed width for "CPU N" label on the left
        font = QFont()
        font.setPixelSize(10)
        font.setFamily("monospace")
        freq_font = QFont()
        freq_font.setPixelSize(9)
        freq_font.setFamily("monospace")
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
            p.drawRoundedRect(x, y, bar_w, bar_h, 4, 4)

            # Filled portion (skip for offline CPUs)
            if not offline and pct > 0:
                fill_w = max(0, int((bar_w - label_w - 2) * pct / 100))
                base_color = _bar_color(pct)
                # Apply temperature tint if available
                temp = self._temps.get(i)
                if temp is not None and temp > 40.0:
                    tint = self._temp_tint(temp)
                    # Blend tint over bar colour
                    t_a = tint.alpha() / 255.0
                    blended = QColor(
                        int(base_color.red()   * (1 - t_a) + tint.red()   * t_a),
                        int(base_color.green() * (1 - t_a) + tint.green() * t_a),
                        int(base_color.blue()  * (1 - t_a) + tint.blue()  * t_a),
                    )
                    p.setBrush(blended)
                else:
                    p.setBrush(base_color)
                p.drawRoundedRect(x + label_w + 1, y + 2,
                                  fill_w, bar_h - 4, 2, 2)

            # Border
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_BORDER, 1))
            p.drawRoundedRect(x, y, bar_w, bar_h, 4, 4)

            # CPU index label
            p.setPen(_OFFLIN if offline else _ONLINE)
            p.drawText(x + 2, y, label_w - 2, bar_h,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       str(i))

            # Percentage text (right-aligned, upper ~16px of bar)
            if offline:
                pct_text = "off"
                p.setPen(_OFFLIN)
            else:
                pct_text = f"{pct:.0f}%"
                p.setPen(QColor(255, 255, 255, 220) if pct >= 50 else _ONLINE)
            p.setFont(font)
            p.drawText(x + label_w + 2, y, bar_w - label_w - 4, bar_h - 10,
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                       pct_text)

            # Frequency text (right-aligned, lower 10px of bar)
            if not offline:
                freq = self._freqs.get(i)
                if freq is not None:
                    p.setFont(freq_font)
                    p.setPen(QColor(180, 200, 220, 160))
                    p.drawText(x + label_w + 2, y + bar_h - 11, bar_w - label_w - 4, 11,
                               Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                               f"{freq:.2f}G")
                    p.setFont(font)

        p.end()


class CpuHistoryWidget(QWidget):
    """36px tall rolling area chart showing overall CPU history above the bars."""

    _HISTORY_LEN = 120   # keep 120 samples (~4 min at 2s refresh)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list[float] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(36)

    @pyqtSlot(list)
    def update_cpu(self, percpu: list[float]):
        avg = sum(percpu) / len(percpu) if percpu else 0.0
        self._history.append(avg)
        if len(self._history) > self._HISTORY_LEN:
            self._history = self._history[-self._HISTORY_LEN:]
        self.update()

    def paintEvent(self, event):
        if not self._history:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Background
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_BG)
        p.drawRect(0, 0, w, h)

        n = len(self._history)
        if n < 2:
            p.end()
            return

        # Compute x positions and y values
        xs = [int(w * i / (self._HISTORY_LEN - 1)) for i in range(self._HISTORY_LEN)]
        # Only use the last n samples, aligned to right edge
        offset = self._HISTORY_LEN - n

        from PyQt6.QtGui import QPainterPath, QPolygonF
        from PyQt6.QtCore import QPointF

        # Build area path
        path = QPainterPath()
        first_x = xs[offset]
        first_y = h - 2 - int((h - 4) * self._history[0] / 100.0)
        path.moveTo(first_x, h - 2)
        path.lineTo(first_x, first_y)

        for i, pct in enumerate(self._history[1:], 1):
            x = xs[offset + i]
            y = h - 2 - int((h - 4) * pct / 100.0)
            path.lineTo(x, y)

        last_x = xs[offset + n - 1]
        path.lineTo(last_x, h - 2)
        path.closeSubpath()

        # Fill with gradient
        grad = QLinearGradient(0, 0, 0, h)
        avg_pct = self._history[-1] if self._history else 0.0
        top_col = _bar_color(avg_pct)
        top_col.setAlpha(180)
        bot_col = QColor(top_col)
        bot_col.setAlpha(40)
        grad.setColorAt(0.0, top_col)
        grad.setColorAt(1.0, bot_col)

        p.setBrush(grad)
        p.drawPath(path)

        # Border at top
        p.setPen(QPen(_BORDER, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(0, 0, w - 1, h - 1)

        p.end()
