"""Hardware detection: Pi model, CSI and USB cameras, sound cards, GPIO pins.

Everything here is read-only and safe to call at any time. Commands that are
missing (development machine, minimal OS) simply yield empty results.
"""
import glob
import json
import os
import re
import shutil
import subprocess

from .config import STATE_DIR

CAMERA_CACHE = os.path.join(STATE_DIR, "csi-cameras.json")

# Sensors the Raspberry Pi kernel has an overlay for. Used when automatic
# detection fails (third-party modules often have no ID EEPROM).
KNOWN_SENSORS = {
    "imx219": "Camera Module 2 and clones (8 MP)",
    "imx708": "Camera Module 3 (12 MP)",
    "imx477": "HQ Camera (12 MP)",
    "imx296": "Global Shutter Camera (1.6 MP)",
    "imx290": "IMX290 / IMX327 / IMX462 starlight modules",
    "imx378": "IMX378 modules (12 MP)",
    "imx500": "AI Camera (12 MP)",
    "ov5647": "Camera Module 1 and clones (5 MP)",
    "ov9281": "OV9281 global shutter modules (1 MP)",
}

# BCM GPIO number -> physical header pin
GPIO_HEADER = {
    2: 3, 3: 5, 4: 7, 14: 8, 15: 10, 17: 11, 18: 12, 27: 13, 22: 15, 23: 16,
    24: 18, 10: 19, 9: 21, 25: 22, 11: 23, 8: 24, 7: 26, 5: 29, 6: 31, 12: 32,
    13: 33, 19: 35, 16: 36, 26: 37, 20: 38, 21: 40,
}
GPIO_NOTES = {
    2: "I2C SDA, has a fixed pull-up", 3: "I2C SCL, has a fixed pull-up",
    14: "UART TX", 15: "UART RX", 18: "PCM clock / PWM0", 12: "PWM0", 13: "PWM1", 19: "PWM1",
    7: "SPI CE1", 8: "SPI CE0", 9: "SPI MISO", 10: "SPI MOSI", 11: "SPI SCLK",
}


def _run(cmd, timeout=15):
    """stdout+stderr of a command, or '' if it is missing or fails to start."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, PermissionError):
        return ""
    return result.stdout + result.stderr


def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


# --- Board -----------------------------------------------------------------

def pi_model():
    return _read("/proc/device-tree/model").strip("\x00\n ") or "Unknown board"


def is_pi5_family():
    model = pi_model()
    return "Raspberry Pi 5" in model or "Compute Module 5" in model or "Raspberry Pi 500" in model


def has_hw_h264_encoder():
    """Pi 1-4 have a hardware H.264 encoder (bcm2835-codec); the Pi 5 does not."""
    for name_file in glob.glob("/sys/class/video4linux/video*/name"):
        if _read(name_file).strip() == "bcm2835-codec-encode":
            return True
    return False


def tuning_dir():
    return "/usr/share/libcamera/ipa/rpi/" + ("pisp" if is_pi5_family() else "vc4")


def boot_config_path():
    for path in ("/boot/firmware/config.txt", "/boot/config.txt"):
        if os.path.exists(path):
            return path
    return None


def boot_camera_setting():
    """Current camera lines of config.txt: {'auto_detect': bool, 'overlays': [...]}"""
    auto_detect = True
    overlays = []
    path = boot_config_path()
    for line in _read(path).splitlines() if path else []:
        line = line.split("#", 1)[0].strip()
        if line.startswith("camera_auto_detect="):
            auto_detect = line.split("=", 1)[1].strip() == "1"
        elif line.startswith("dtoverlay="):
            overlay = line.split("=", 1)[1].strip()
            if overlay.split(",")[0] in KNOWN_SENSORS:
                overlays.append(overlay)
    return {"auto_detect": auto_detect, "overlays": overlays}


# --- CSI cameras -----------------------------------------------------------

_CAM_RE = re.compile(r"^\s*(\d+)\s*:\s*(\S+)\s*\[(\d+)x(\d+)[^\]]*\]\s*\((.+)\)")
_FORMAT_RE = re.compile(r"'([A-Z0-9_]+)'\s*:")
_MODE_RE = re.compile(r"(\d+)x(\d+)\s*\[([\d.]+)\s*fps\s*-\s*\((\d+),\s*(\d+)\)/(\d+)x(\d+)\s*crop\]")


def parse_camera_list(text):
    """Parse `rpicam-hello --list-cameras`.

    While a camera is streaming, libcamera still lists it but reports dummy
    modes (crop 65535x65535): those modes are dropped and 'busy' is set.
    """
    cameras = []
    cam = None
    bit_depth = packing = None
    for line in text.splitlines():
        m = _CAM_RE.match(line)
        if m:
            cam = {
                "index": int(m.group(1)), "sensor": m.group(2),
                "width": int(m.group(3)), "height": int(m.group(4)),
                "path": m.group(5), "modes": [], "busy": False,
            }
            cameras.append(cam)
            bit_depth = packing = None
            continue
        if cam is None:
            continue
        f = _FORMAT_RE.search(line)
        if f:
            fmt = f.group(1)
            depth = re.search(r"(\d+)", fmt.split("_")[0])
            bit_depth = int(depth.group(1)) if depth else 10
            packing = "P" if fmt.endswith("_CSI2P") else "U"
        for mode in _MODE_RE.finditer(line):
            w, h, fps, _x, _y, cw, ch = mode.groups()
            if int(cw) == 65535:
                cam["busy"] = True
                continue
            # Keep only the highest bit depth, listed first
            if cam["modes"] and bit_depth < cam["modes"][0]["bit_depth"]:
                continue
            cam["modes"].append({
                "width": int(w), "height": int(h), "fps": float(fps),
                "full_fov": int(cw) >= cam["width"] - 16 and int(ch) >= cam["height"] - 16,
                "bit_depth": bit_depth,
                "mode": f"{w}:{h}:{bit_depth}:{packing}",
            })
    return cameras


def _load_camera_cache():
    try:
        return json.loads(_read(CAMERA_CACHE) or "[]")
    except ValueError:
        return []


def _save_camera_cache(cameras):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(CAMERA_CACHE, "w") as f:
            json.dump(cameras, f, indent=1)
    except OSError:
        pass


def csi_cameras():
    tool = shutil.which("rpicam-hello") or shutil.which("libcamera-hello")
    if not tool:
        return []
    cameras = parse_camera_list(_run([tool, "--list-cameras"], timeout=20))
    cache = {(c["index"], c["sensor"]): c for c in _load_camera_cache()}
    for cam in cameras:
        if cam["modes"] and not cam["busy"]:
            continue
        cached = cache.get((cam["index"], cam["sensor"]))
        if cached:
            cam["modes"] = cached["modes"]
    if cameras and all(c["modes"] for c in cameras):
        _save_camera_cache(cameras)
    return cameras


# --- USB cameras -----------------------------------------------------------

_FOURCC = {"H264": "h264", "MJPG": "mjpeg", "YUYV": "yuyv"}


def parse_v4l2_formats(text):
    """`v4l2-ctl --list-formats-ext` -> {'mjpeg': {'1280x720': [30.0, 15.0]}, ...}"""
    formats = {}
    current = size = None
    for line in text.splitlines():
        m = re.search(r"\[\d+\]:\s*'(\w+)'", line)
        if m:
            current = _FOURCC.get(m.group(1))
            if current:
                formats.setdefault(current, {})
            size = None
            continue
        if current is None:
            continue
        m = re.search(r"Size:\s*\w+\s+(\d+x\d+)", line)
        if m:
            size = m.group(1)
            formats[current].setdefault(size, [])
            continue
        m = re.search(r"\(([\d.]+)\s*fps\)", line)
        if m and size:
            fps = float(m.group(1))
            if fps not in formats[current][size]:
                formats[current][size].append(fps)
    return formats


def usb_cameras():
    cameras = []
    for path in sorted(glob.glob("/dev/v4l/by-id/*-video-index0")):
        info = _run(["v4l2-ctl", "-d", path, "--info"])
        m = re.search(r"Card type\s*:\s*(.+)", info)
        formats = parse_v4l2_formats(_run(["v4l2-ctl", "-d", path, "--list-formats-ext"]))
        if not formats:
            continue
        cameras.append({
            "path": path,
            "name": m.group(1).strip() if m else os.path.basename(path),
            "formats": formats,
        })
    return cameras


# --- Sound -----------------------------------------------------------------

_CARD_RE = re.compile(r"^\s*(\d+)\s+\[(\S+)\s*\]:\s*(.+?)\s+-\s+(.+)$")


def sound_cards():
    """[{'index', 'id', 'name', 'capture': [dev...], 'playback': [dev...]}]"""
    cards = []
    for line in _read("/proc/asound/cards").splitlines():
        m = _CARD_RE.match(line)
        if not m:
            continue
        index, card_id = int(m.group(1)), m.group(2)
        # HDMI outputs only accept IEC958 frames and do not work as a plain speaker
        if card_id.startswith("vc4hdmi"):
            continue
        card = {"index": index, "id": card_id, "name": m.group(4).strip(), "capture": [], "playback": []}
        for pcm in sorted(glob.glob(f"/proc/asound/card{index}/pcm*[cp]")):
            pm = re.search(r"pcm(\d+)([cp])$", pcm)
            if pm:
                card["capture" if pm.group(2) == "c" else "playback"].append(int(pm.group(1)))
        cards.append(card)
    return cards


def audio_devices(kind):
    """kind = 'capture' | 'playback' -> [{'key': 'Headphones,0', 'label': ...}]"""
    devices = []
    for card in sound_cards():
        for dev in card[kind]:
            devices.append({
                "key": f"{card['id']},{dev}",
                "label": f"{card['name']} (card {card['index']}, device {dev})",
                "card": card["index"],
            })
    return devices


def alsa_pcm_path(key, kind):
    """'Headphones,0' -> '/dev/snd/pcmC0D0p'. Card numbers change between boots
    when USB devices are plugged, so the card is stored by its ID and resolved here."""
    if not key or "," not in key:
        return None
    card_id, dev = key.rsplit(",", 1)
    for card in sound_cards():
        if card["id"] == card_id and int(dev) in card[kind]:
            return f"/dev/snd/pcmC{card['index']}D{dev}{'c' if kind == 'capture' else 'p'}"
    return None


def card_index(key):
    if not key or "," not in key:
        return None
    card_id = key.rsplit(",", 1)[0]
    for card in sound_cards():
        if card["id"] == card_id:
            return card["index"]
    return None


def mixer_control(card, kind):
    """First simple mixer control of a card that has a playback/capture volume."""
    wanted = "pvolume" if kind == "playback" else "cvolume"
    for name in re.findall(r"Simple mixer control '([^']+)',\d+", _run(["amixer", "-c", str(card), "scontrols"])):
        caps = _run(["amixer", "-c", str(card), "sget", name])
        if wanted in caps:
            return name
    return None


def get_volume(card, kind):
    control = mixer_control(card, kind)
    if not control:
        return None
    m = re.search(r"\[(\d+)%\]", _run(["amixer", "-c", str(card), "sget", control]))
    return int(m.group(1)) if m else None


def set_volume(card, kind, percent):
    control = mixer_control(card, kind)
    if not control:
        return False
    _run(["amixer", "-q", "-c", str(card), "sset", control, f"{int(percent)}%"])
    return True


# --- Summary ---------------------------------------------------------------

def scan():
    return {
        "model": pi_model(),
        "pi5": is_pi5_family(),
        "hw_encoder": has_hw_h264_encoder(),
        "tuning_dir": tuning_dir(),
        "boot_camera": boot_camera_setting(),
        "csi": csi_cameras(),
        "usb": usb_cameras(),
        "capture": audio_devices("capture"),
        "playback": audio_devices("playback"),
    }
