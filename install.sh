#!/bin/bash
# Visiophone installer for Raspberry Pi OS (Bookworm or Trixie, Lite or Desktop).
# Usage: sudo ./install.sh            install or update
# Environment: GO2RTC_VERSION=1.9.15  go2rtc release to install
#              NONINTERACTIVE=1       do not ask for the web password
set -euo pipefail

GO2RTC_VERSION="${GO2RTC_VERSION:-1.9.15}"
PREFIX=/opt/visiophone
CONFIG_DIR=/etc/visiophone
SRC="$(cd "$(dirname "$0")" && pwd)"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run it with sudo: sudo ./install.sh"
[ -f /etc/os-release ] && . /etc/os-release
case "${VERSION_CODENAME:-}" in
    bookworm|trixie) ;;
    *) warn "Untested system (${PRETTY_NAME:-unknown}). Raspberry Pi OS Bookworm or Trixie is expected." ;;
esac
MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "unknown")
say "Board: $MODEL"

# --- Packages -----------------------------------------------------------------
say "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
RPICAM=rpicam-apps-lite
dpkg -s rpicam-apps >/dev/null 2>&1 && RPICAM=rpicam-apps
apt-get install -y -qq --no-install-recommends \
    python3 python3-flask python3-waitress python3-markdown python3-yaml python3-paho-mqtt \
    python3-gpiozero python3-lgpio python3-pip \
    "$RPICAM" v4l-utils alsa-utils ffmpeg curl sudo

# paho-mqtt 2.x is needed (Bookworm ships 1.6): install it next to the program if required
VENDOR="$PREFIX/vendor"
if ! python3 -c 'import paho.mqtt.client as m, sys; sys.exit(0 if hasattr(m, "CallbackAPIVersion") else 1)' 2>/dev/null; then
    say "Installing paho-mqtt 2 in $VENDOR"
    python3 -m pip install --quiet --target "$VENDOR" --upgrade "paho-mqtt>=2,<3"
fi

# --- go2rtc -------------------------------------------------------------------
case "$(uname -m)" in
    aarch64) ARCH=arm64 ;;
    armv7l)  ARCH=arm ;;
    armv6l)  ARCH=armv6 ;;
    x86_64)  ARCH=amd64 ;;
    *) die "unsupported architecture $(uname -m)" ;;
esac
CURRENT=$(/usr/local/bin/go2rtc -version 2>/dev/null | awk '{print $3}' || true)
if [ "$CURRENT" != "$GO2RTC_VERSION" ]; then
    say "Installing go2rtc $GO2RTC_VERSION ($ARCH)"
    curl -fsSL -o /tmp/go2rtc.new \
        "https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VERSION}/go2rtc_linux_${ARCH}"
    install -m 0755 /tmp/go2rtc.new /usr/local/bin/go2rtc
    rm -f /tmp/go2rtc.new
else
    say "go2rtc $CURRENT already installed"
fi

# --- Service user -------------------------------------------------------------
if ! id visiophone >/dev/null 2>&1; then
    say "Creating the visiophone system user"
    useradd --system --home-dir /var/lib/visiophone --shell /usr/sbin/nologin visiophone
fi
for group in video audio gpio render systemd-journal; do
    getent group "$group" >/dev/null && usermod -aG "$group" visiophone
done

# --- Program files ------------------------------------------------------------
say "Installing the program in $PREFIX"
mkdir -p "$PREFIX"
rm -rf "$PREFIX/visiophone" "$PREFIX/docs"
cp -r "$SRC/visiophone" "$SRC/docs" "$PREFIX/"
find "$PREFIX" -name __pycache__ -prune -exec rm -rf {} +
chown -R root:root "$PREFIX"
install -m 0755 "$SRC/system/visiophone-admin" /usr/local/sbin/visiophone-admin
install -m 0755 "$SRC/system/visiophone-passwd" /usr/local/bin/visiophone-passwd

install -m 0440 "$SRC/system/visiophone.sudoers" /tmp/visiophone.sudoers
visudo -cf /tmp/visiophone.sudoers >/dev/null || die "invalid sudoers file"
mv /tmp/visiophone.sudoers /etc/sudoers.d/visiophone

mkdir -p "$CONFIG_DIR" /var/lib/visiophone
chown visiophone:visiophone "$CONFIG_DIR" /var/lib/visiophone
chmod 0750 "$CONFIG_DIR"

for unit in visiophone.service go2rtc.service; do
    if [ -f "/etc/systemd/system/$unit" ] && ! cmp -s "$SRC/system/$unit" "/etc/systemd/system/$unit"; then
        cp "/etc/systemd/system/$unit" "/etc/systemd/system/$unit.before-visiophone"
        warn "Existing $unit saved as $unit.before-visiophone"
    fi
    install -m 0644 "$SRC/system/$unit" "/etc/systemd/system/$unit"
done
if [ -d "$VENDOR" ]; then
    sed -i "s|^Environment=PYTHONPATH=.*|Environment=PYTHONPATH=$PREFIX:$VENDOR|" /etc/systemd/system/visiophone.service
fi
systemctl daemon-reload

run_as_service() {
    (cd "$PREFIX" && sudo -u visiophone PYTHONPATH="$PREFIX:$VENDOR" python3 -m visiophone "$@")
}

# --- First configuration ------------------------------------------------------
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    say "Detecting the hardware"
    systemctl stop go2rtc 2>/dev/null || true   # frees the camera so its modes can be read
    run_as_service autoconfig
fi
# go2rtc must start with a valid file; the service rewrites it at start
if [ ! -s "$CONFIG_DIR/go2rtc.yaml" ]; then
    run_as_service go2rtc 2>/dev/null | sudo -u visiophone tee "$CONFIG_DIR/go2rtc.yaml" > /dev/null
fi

if ! grep -q '^  password_hash: .\+' "$CONFIG_DIR/config.yaml" 2>/dev/null; then
    if [ -z "${NONINTERACTIVE:-}" ] && [ -t 0 ]; then
        say "Choose the password of the web interface"
        run_as_service passwd
    else
        warn "No web password set: run 'sudo visiophone-passwd'."
    fi
fi

# --- Boot settings ------------------------------------------------------------
BOOT_CFG=/boot/firmware/config.txt
[ -f "$BOOT_CFG" ] || BOOT_CFG=/boot/config.txt
case "$MODEL" in
    *"Pi 5"*|*"Pi 4"*|*"Pi 400"*|*"Compute Module 4"*|*"Compute Module 5"*) ;;
    *)
        # Older boards: the hardware H.264 codec needs GPU memory
        if [ -f "$BOOT_CFG" ] && ! grep -q '^gpu_mem=' "$BOOT_CFG"; then
            printf '\n[all]\n# Visiophone: memory for the hardware video codec\ngpu_mem=128\n' >> "$BOOT_CFG"
            warn "gpu_mem=128 added to $BOOT_CFG: reboot to apply it."
        fi
        ;;
esac

# --- Start --------------------------------------------------------------------
say "Starting the services"
systemctl enable --now go2rtc.service >/dev/null
systemctl enable visiophone.service >/dev/null
systemctl restart visiophone.service

IP=$(hostname -I | awk '{print $1}')
echo
say "Done. Open http://${IP:-<pi-address>}/ in a browser."
grep -qs '^camera_auto_detect=0' "$BOOT_CFG" || \
    echo "    No CSI camera found? Declare it on the Camera page (third-party modules are often not detected)."
