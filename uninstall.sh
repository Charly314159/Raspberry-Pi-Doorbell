#!/bin/bash
# Remove Visiophone. The configuration is kept unless --purge is given.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run it with sudo: sudo ./uninstall.sh [--purge]" >&2; exit 1; }

systemctl disable --now visiophone.service go2rtc.service 2>/dev/null || true
rm -f /etc/systemd/system/visiophone.service /etc/systemd/system/go2rtc.service
systemctl daemon-reload
rm -rf /opt/visiophone
rm -f /usr/local/sbin/visiophone-admin /usr/local/bin/visiophone-passwd /etc/sudoers.d/visiophone

if [ "${1:-}" = "--purge" ]; then
    rm -rf /etc/visiophone /var/lib/visiophone /usr/local/bin/go2rtc
    userdel visiophone 2>/dev/null || true
    echo "Visiophone removed, configuration and go2rtc included."
else
    echo "Visiophone removed. Configuration kept in /etc/visiophone (use --purge to delete it)."
fi
echo "The camera block in config.txt is left in place; remove the 'visiophone camera' lines by hand if needed."
