"""Configuration file: /etc/visiophone/config.yaml.

The file only needs to contain the values that differ from DEFAULTS; missing
keys are filled in at load time, so new options can be added in later versions
without breaking existing installations.
"""
import copy
import os
import tempfile
import threading

import yaml

CONFIG_DIR = os.environ.get("VISIOPHONE_CONFIG_DIR", "/etc/visiophone")
STATE_DIR = os.environ.get("VISIOPHONE_STATE_DIR", "/var/lib/visiophone")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")

DEFAULTS = {
    "device": {
        # Name shown in Home Assistant
        "name": "Doorbell",
        # Prefix of MQTT topics, unique IDs and entity IDs: change it only before
        # the first connection to Home Assistant, or the entities get renamed.
        "id": "visiophone",
    },
    "camera": {
        "source": "none",          # none | csi | usb
        "csi_index": 0,
        "usb_device": "",          # /dev/v4l/by-id/... path
        "usb_format": "h264",      # h264 | mjpeg | yuyv
        "width": 1280,
        "height": 720,
        "framerate": 15,
        "bitrate_kbps": 2500,
        "rotation": 0,             # 0 | 180
        "hflip": False,
        "vflip": False,
        "noir": False,             # CSI only: tuning for sensors without IR filter
        "full_fov": True,          # CSI only: use a sensor mode covering the whole sensor
        "web_transcode": False,    # re-encode for browsers (USB cameras with Main/High profile)
        # Image tuning (CSI cameras): neutral values add nothing to the command line
        "brightness": 0.0,         # -1.0 .. 1.0
        "contrast": 1.0,           # 0 .. 2
        "saturation": 1.0,         # 0 .. 2 (0 = black and white)
        "sharpness": 1.0,          # 0 .. 2
        "ev": 0.0,                 # exposure compensation, -4 .. 4
        "metering": "centre",      # centre | spot | average
        "awb": "auto",             # auto | incandescent | tungsten | fluorescent | indoor | daylight | cloudy | manual
        "awb_red": 1.5,            # manual white balance gains
        "awb_blue": 1.5,
        "denoise": "auto",         # auto | off | cdn_off | cdn_fast | cdn_hq
    },
    "audio": {
        "microphone": "",          # ALSA "card_id,device", e.g. "Device,0"; empty = none
        "speaker": "",
        # Play the browser's voice through Visiophone's relay, which also
        # measures its level for the LED ("follow the sound"); off = go2rtc
        # writes straight to the sound card.
        "speaker_relay": True,
    },
    "streams": {
        "name": "doorbell",
        "username": "",            # optional go2rtc credentials (RTSP and API)
        "password": "",
        # Let web pages served elsewhere (Home Assistant dashboards) open the
        # go2rtc player, e.g. the Advanced Camera Card with its go2rtc provider
        "cross_origin": False,
    },
    "button": {
        "enabled": True,
        "gpio": 17,
        "pull_up": True,           # button between the GPIO and GND
        "debounce_ms": 50,
        "min_interval_s": 2.0,     # ignore new presses during this delay
    },
    "led": {
        "enabled": False,
        "gpio": 27,
        "on_at_start": True,
        "brightness": 255,         # 1-255
        "blink_on_ring": True,
        "blink_duration_s": 10,
        "follow_sound": False,     # the LED follows the sound while the doorbell speaks
    },
    "mqtt": {
        "enabled": False,
        "host": "",
        "port": 1883,
        "username": "",
        "password": "",
        "discovery_prefix": "homeassistant",
        "status_interval_s": 60,
    },
    "web": {
        "port": 80,
        "password_hash": "",
    },
}


def deep_merge(base, override):
    """Copy of base with the values of override, recursively for dicts."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self.data = self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                user = yaml.safe_load(f) or {}
        except FileNotFoundError:
            user = {}
        return deep_merge(DEFAULTS, user)

    def section(self, name):
        with self._lock:
            return copy.deepcopy(self.data[name])

    def update(self, name, values):
        with self._lock:
            self.data[name].update(values)
            self.save()

    def save(self):
        """Atomic write, readable by the service user only (it holds passwords)."""
        with self._lock:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-")
            try:
                with os.fdopen(fd, "w") as f:
                    f.write("# Visiophone configuration, edited by the web interface.\n")
                    yaml.safe_dump(self.data, f, sort_keys=False, allow_unicode=True)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                os.unlink(tmp)
                raise
