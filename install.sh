#!/bin/bash
# Process Lasso installer
# Run from the directory containing this script, or from any directory.
# Usage: bash install.sh [--uninstall]

set -e

INSTALL_DIR="$HOME/.local/share/process-lasso"
LAUNCHER="/usr/local/bin/process-lasso"
DESKTOP="$HOME/.local/share/applications/process-lasso.desktop"
SERVICE="$HOME/.config/systemd/user/process-lasso.service"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[!]${NC} $*"; exit 1; }

# ── Package manager detection ──────────────────────────────────────────────
_PKG_MGR=""
detect_pkg_manager() {
    command -v pacman &>/dev/null && _PKG_MGR=pacman && return
    command -v apt    &>/dev/null && _PKG_MGR=apt    && return
    command -v dnf    &>/dev/null && _PKG_MGR=dnf    && return
    command -v zypper &>/dev/null && _PKG_MGR=zypper && return
    _PKG_MGR=pip
}
pkg_hint() {   # pkg_hint <pacman> <apt> <dnf> <zypper> <pip>
    case "$_PKG_MGR" in
        pacman) echo "sudo pacman -S $1" ;;
        apt)    echo "sudo apt install python3-$2" ;;
        dnf)    echo "sudo dnf install python3-$3" ;;
        zypper) echo "sudo zypper install python3-$4" ;;
        *)      echo "pip install $5" ;;
    esac
}
detect_pkg_manager

# ── Uninstall ──────────────────────────────────────────────────────────────
if [[ "$1" == "--uninstall" ]]; then
    info "Stopping and disabling service..."
    if command -v systemctl &>/dev/null; then
        systemctl --user stop  process-lasso.service 2>/dev/null || true
        systemctl --user disable process-lasso.service 2>/dev/null || true
    fi
    info "Removing files..."
    rm -f "$DESKTOP" "$SERVICE"
    sudo rm -f "$LAUNCHER" 2>/dev/null || warn "Could not remove $LAUNCHER (run with sudo manually)"
    info "Config preserved at ~/.config/process-lasso/ — remove manually if desired."
    info "App files preserved at $INSTALL_DIR — remove manually if desired."
    if command -v systemctl &>/dev/null; then
        systemctl --user daemon-reload
    fi
    info "Uninstall complete."
    exit 0
fi

# ── Dependency check ───────────────────────────────────────────────────────
info "Checking dependencies..."
python3 -c "import psutil" 2>/dev/null || error "python-psutil missing. Install: $(pkg_hint python-psutil psutil psutil psutil psutil)"
python3 -c "import PyQt6"  2>/dev/null || error "python-pyqt6 missing.  Install: $(pkg_hint python-pyqt6  pyqt6 PyQt6  python3-pyqt6 PyQt6)"
info "Dependencies OK."

# ── Copy app files ─────────────────────────────────────────────────────────
if [[ "$SCRIPT_DIR" != "$INSTALL_DIR" ]]; then
    info "Installing app to $INSTALL_DIR ..."
    mkdir -p "$INSTALL_DIR/gui"
    cp "$SCRIPT_DIR"/*.py "$INSTALL_DIR/"
    cp "$SCRIPT_DIR"/gui/*.py "$INSTALL_DIR/gui/"
    info "App files installed."
else
    info "Already running from $INSTALL_DIR — skipping copy."
fi

# ── Launcher ───────────────────────────────────────────────────────────────
info "Installing launcher to $LAUNCHER ..."
LAUNCHER_CONTENT="#!/bin/bash
exec python3 $INSTALL_DIR/main.py \"\$@\"
"
if command -v sudo &>/dev/null; then
    echo "$LAUNCHER_CONTENT" | sudo tee "$LAUNCHER" > /dev/null
    sudo chmod +x "$LAUNCHER"
    info "Launcher installed."
else
    warn "sudo not available. Manually create $LAUNCHER:"
    echo "  echo '$LAUNCHER_CONTENT' > $LAUNCHER && chmod +x $LAUNCHER"
fi

# ── Desktop entry ──────────────────────────────────────────────────────────
info "Installing desktop entry..."
mkdir -p "$(dirname "$DESKTOP")"
cat > "$DESKTOP" << EOF
[Desktop Entry]
Name=Process Lasso
Comment=Linux process manager with CPU affinity, nice priority, and ProBalance
Exec=$LAUNCHER
Icon=utilities-system-monitor
Terminal=false
Type=Application
Categories=System;Monitor;
StartupNotify=false
EOF
info "Desktop entry installed."

# ── Systemd service ────────────────────────────────────────────────────────
if command -v systemctl &>/dev/null; then
    info "Installing systemd user service..."
    mkdir -p "$(dirname "$SERVICE")"
    cat > "$SERVICE" << EOF
[Unit]
Description=Process Lasso
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart=$LAUNCHER
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF

    systemctl --user daemon-reload
    systemctl --user enable process-lasso.service
    info "Service enabled (starts automatically with graphical session)."
else
    warn "systemd not available — autostart not configured. Run 'process-lasso' manually or set up autostart via your DE."
fi

# ── CPU topology detection + privileged helper ─────────────────────────────
info "Detecting CPU topology..."
python3 -c "
import sys
sys.path.insert(0, '$INSTALL_DIR')
import cpu_park
topo = cpu_park.detect_topology()
print(topo.description)
if topo.has_asymmetry:
    pref_str = cpu_park._fmt(topo.preferred)
    npref_str = cpu_park._fmt(topo.non_preferred)
    print(f'  Preferred CPUs  (game):  {pref_str}')
    print(f'  Non-preferred   (background): {npref_str}')
    print('Gaming Mode (CPU parking) will be available.')
else:
    print('Uniform CPU topology — Gaming Mode parking not applicable.')
"

info "Installing privileged sysfs helper (requires root)..."
python3 -c "
import sys, os, getpass
sys.path.insert(0, '$INSTALL_DIR')
import cpu_park
username = os.environ.get('USER') or ''
password = getpass.getpass('Root password: ')
ok, msg = cpu_park.install_helper_as_root(username=username, password=password)
print(msg)
if ok:
    print('Helper installed. sudo NOPASSWD rule created.')
else:
    print('WARNING: Helper install failed. Gaming Mode (CPU parking) will not work.')
    print('You can retry from the Gaming Mode tab in the app.')
"

# ── Done ───────────────────────────────────────────────────────────────────
echo
echo -e "${GREEN}Installation complete!${NC}"
echo
echo "  Start now:      process-lasso"
echo "  Start service:  systemctl --user start process-lasso.service"
echo "  Uninstall:      bash $INSTALL_DIR/install.sh --uninstall"
echo
echo "Quick start for asymmetric CPUs (AMD X3D / Intel Hybrid):"
echo "  Gaming Mode tab → click 'Enable Gaming Mode'"
echo "  This parks non-preferred CPUs before launching your game"
echo "  (exactly what gamemoderun does)"
echo
echo "Optional — background process isolation:"
echo "  Settings tab → Default CPU Affinity: non-preferred cpulist"
echo "  Rules tab    → steam (exact) → preferred cpulist"
