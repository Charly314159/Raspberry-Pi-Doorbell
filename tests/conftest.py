import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GPIOZERO_PIN_FACTORY", "mock")


@pytest.fixture
def config(tmp_path, monkeypatch):
    from visiophone import config as config_module
    return config_module.Config(str(tmp_path / "config.yaml"))


@pytest.fixture
def hw():
    from visiophone import hardware
    return {
        "model": "Raspberry Pi 3 Model B Rev 1.2", "pi5": False, "hw_encoder": True,
        "tuning_dir": "/nonexistent", "boot_camera": {"auto_detect": False, "overlays": ["imx219"]},
        "csi": hardware.parse_camera_list(__import__("tests.test_hardware", fromlist=["IDLE"]).IDLE),
        "usb": [{"path": "/dev/v4l/by-id/usb-cam-video-index0", "name": "USB cam",
                 "formats": {"h264": {"1280x720": [30.0]}, "mjpeg": {"1280x720": [30.0]}}}],
        "capture": [], "playback": [{"key": "Headphones,0", "label": "Headphones", "card": 0}],
    }
