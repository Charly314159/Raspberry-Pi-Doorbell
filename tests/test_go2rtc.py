import yaml

from visiophone import go2rtc


def render(config, hw, monkeypatch, mic=None, speaker=None):
    paths = {"capture": mic, "playback": speaker}
    monkeypatch.setattr(go2rtc.system, "alsa_path_or_warn", lambda key, kind, warnings: paths[kind])
    text, warnings = go2rtc.render(config, hw)
    return yaml.safe_load(text), warnings


def test_no_camera(config, hw, monkeypatch):
    data, warnings = render(config, hw, monkeypatch)
    assert data["streams"] == {} and warnings


def test_csi_full_fov(config, hw, monkeypatch):
    config.update("camera", {"source": "csi", "width": 1280, "height": 960, "framerate": 15, "rotation": 180})
    config.update("audio", {"speaker": "Headphones,0"})
    data, warnings = render(config, hw, monkeypatch, speaker="/dev/snd/pcmC0D0p")
    src = data["streams"]["doorbell"][0]
    assert "--mode 1640:1232:10:P" in src and "--rotation 180" in src and "--profile baseline" in src
    assert "--level 4 " in src
    assert data["streams"]["doorbell_web"] == ["rtsp://127.0.0.1:8554/doorbell", "exec:/usr/local/bin/visiophone-speaker --device plughw:CARD=Headphones,DEV=0#backchannel=1#audio=pcma/8000"]
    assert data["streams"]["doorbell_talk"] == [data["streams"]["doorbell_web"][1]]
    config.update("audio", {"speaker_relay": False})
    data, _ = render(config, hw, monkeypatch, speaker="/dev/snd/pcmC0D0p")
    assert data["streams"]["doorbell_web"][1] == "alsa:device?audio=/dev/snd/pcmC0D0p"
    assert warnings == []


def test_csi_mic_and_auth(config, hw, monkeypatch):
    config.update("camera", {"source": "csi"})
    config.update("audio", {"microphone": "Device,0"})
    config.update("streams", {"username": "u", "password": "p@ss"})
    data, _ = render(config, hw, monkeypatch, mic="/dev/snd/pcmC1D0c")
    assert data["streams"]["doorbell"][1] == "alsa:device?audio=/dev/snd/pcmC1D0c"
    web = data["streams"]["doorbell_web"][0]
    assert "rtsp://u:p%40ss@127.0.0.1:8554/doorbell" in web and "-c:v copy" in web and "libopus" in web
    assert data["rtsp"]["username"] == "u" and data["api"]["password"] == "p@ss"


def test_usb_h264(config, hw, monkeypatch):
    config.update("camera", {"source": "usb", "usb_device": "/dev/v4l/by-id/usb-cam-video-index0", "usb_format": "h264"})
    data, _ = render(config, hw, monkeypatch)
    assert data["streams"]["doorbell"][0].startswith("exec:v4l2-ctl -d /dev/v4l/by-id/usb-cam-video-index0")


def test_usb_mjpeg_pi5(config, hw, monkeypatch):
    hw["hw_encoder"] = False
    config.update("camera", {"source": "usb", "usb_device": "/dev/v4l/by-id/usb-cam-video-index0", "usb_format": "mjpeg",
                             "web_transcode": True})
    data, _ = render(config, hw, monkeypatch)
    assert "-input_format mjpeg" in data["streams"]["doorbell"][0] and "libx264" in data["streams"]["doorbell"][0]


def test_missing_usb_camera(config, hw, monkeypatch):
    config.update("camera", {"source": "usb", "usb_device": "/dev/v4l/by-id/other"})
    data, warnings = render(config, hw, monkeypatch)
    assert "not found" in warnings[0]


def test_live_viewers_ignores_rtsp():
    streams = {
        "doorbell": {"consumers": [{"format_name": "rtsp"}, {"format_name": "webrtc/json"}]},
        "doorbell_web": {"consumers": [{"format_name": "mse/fmp4"}]},
        "other": {"consumers": None},
    }
    assert go2rtc.live_viewers(streams) == 2
    streams["doorbell_talk"] = {"consumers": [{"format_name": "webrtc/json"}]}
    assert go2rtc.live_viewers(streams) == 2
    assert go2rtc.live_viewers(None) == 0


def test_image_args(config, hw, monkeypatch):
    assert go2rtc.image_args(config.section("camera")) == []
    config.update("camera", {"source": "csi", "brightness": 0.1, "saturation": 1.3, "ev": -0.5,
                             "awb": "manual", "awb_red": 1.8, "awb_blue": 1.2, "denoise": "cdn_hq"})
    args = go2rtc.image_args(config.section("camera"))
    assert args == ["--brightness 0.1", "--saturation 1.3", "--ev -0.5", "--awbgains 1.8,1.2", "--denoise cdn_hq"]
    data, _ = render(config, hw, monkeypatch)
    assert "--awbgains 1.8,1.2" in data["streams"]["doorbell"][0]
    config.update("camera", {"awb": "indoor"})
    assert "--awb indoor" in go2rtc.image_args(config.section("camera"))


def test_test_tone_microphone(config, hw, monkeypatch):
    config.update("camera", {"source": "csi"})
    config.update("audio", {"microphone": "test-tone"})
    data, warnings = render(config, hw, monkeypatch)
    assert "aevalsrc=exprs=0.25*sin(2*PI*880*t)*lt(mod(t\\,1)\\,0.2)" in data["streams"]["doorbell"][1]
    assert "libopus" in data["streams"]["doorbell_web"][0]
