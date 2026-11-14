"""System state and privileged actions.

Privileged actions go through /usr/local/sbin/visiophone-admin, the only
command the service user may run with sudo (see system/visiophone.sudoers).
"""
import os
import shutil
import socket
import subprocess
import time
from datetime import datetime

from . import hardware

ADMIN = "/usr/local/sbin/visiophone-admin"


def admin(*args, timeout=30):
    """Run a privileged action. Returns (ok, message)."""
    try:
        result = subprocess.run(["sudo", "-n", ADMIN, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def alsa_path_or_warn(key, kind, warnings):
    path = hardware.alsa_pcm_path(key, kind)
    if path is None:
        warnings.append(f"Sound device {key} ({kind}) not found.")
    return path


def service_active(unit):
    try:
        out = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.strip() == "active"


def boot_time():
    with open("/proc/uptime") as f:
        started = time.time() - float(f.read().split()[0])
    return datetime.fromtimestamp(round(started)).astimezone()


def throttled():
    """vcgencmd get_throttled bits, or None when unavailable."""
    if not shutil.which("vcgencmd"):
        return None
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5).stdout
        return int(out.strip().split("=")[1], 16)
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def metrics():
    with open("/sys/class/thermal/thermal_zone0/temp") as f:
        temperature = int(f.read()) / 1000
    with open("/proc/meminfo") as f:
        meminfo = {line.split(":")[0]: int(line.split()[1]) for line in f}
    disk = shutil.disk_usage("/")
    bits = throttled()
    return {
        "cpu_temperature": round(temperature, 1),
        "cpu_load": round(os.getloadavg()[0] / os.cpu_count() * 100, 1),
        "memory_used": round((1 - meminfo["MemAvailable"] / meminfo["MemTotal"]) * 100, 1),
        "disk_used": round(disk.used / disk.total * 100, 1),
        "under_voltage": bool(bits & 0x1) if bits is not None else None,
        "under_voltage_since_boot": bool(bits & 0x10000) if bits is not None else None,
        "go2rtc": service_active("go2rtc"),
    }


def ip_address():
    """Address used to reach the LAN (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def logs(lines=200):
    try:
        out = subprocess.run(
            ["journalctl", "-u", "visiophone", "-u", "go2rtc", "-n", str(lines), "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    return out.stdout or out.stderr


def play_test_tone(alsa_device, seconds=1.5, frequency=660, on_level=None):
    """Play a short tone on an ALSA device. Returns (ok, message).
    on_level(0..1) is called every 40 ms with the level played (LED follows it)."""
    import math
    import struct

    from .sound import level_from_s16le

    rate = 8000
    chunk = int(rate * 0.04)
    total = int(rate * seconds)
    fade = rate // 50
    try:
        player = subprocess.Popen(["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(rate), "-c", "1",
                                   "-D", alsa_device, "-"], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        return False, str(e)
    try:
        for start in range(0, total, chunk):
            samples = []
            for i in range(start, min(start + chunk, total)):
                envelope = min(1.0, i / fade, (total - i) / fade)
                samples.append(int(12000 * envelope * math.sin(2 * math.pi * frequency * i / rate)))
            data = struct.pack(f"<{len(samples)}h", *samples)
            player.stdin.write(data)       # blocks at playback speed
            if on_level:
                on_level(level_from_s16le(data))
        player.stdin.close()
    except BrokenPipeError:
        pass
    finally:
        if on_level:
            on_level(0.0)
    try:
        player.wait(5)
    except subprocess.TimeoutExpired:
        player.kill()
    if player.returncode:
        message = player.stderr.read().decode(errors="replace").strip()
        if "busy" in message.lower():
            message = "The speaker is in use (a browser or a voice message is playing through it)."
        return False, message
    return True, ""
