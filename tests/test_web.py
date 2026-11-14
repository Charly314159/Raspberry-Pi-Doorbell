import re

import pytest

from visiophone import web
from visiophone.service import Visiophone


@pytest.fixture
def client(config, hw, tmp_path, monkeypatch):
    monkeypatch.setattr(web, "SECRET_KEY_PATH", str(tmp_path / "secret"))
    monkeypatch.setattr(web.go2rtc, "api_streams", lambda cfg: {"doorbell": {"producers": [], "consumers": []}})
    monkeypatch.setattr(web.system, "metrics", lambda: {
        "cpu_temperature": 45.0, "cpu_load": 3.0, "memory_used": 30.0, "disk_used": 20.0,
        "under_voltage": False, "under_voltage_since_boot": False, "go2rtc": True})
    calls = []
    monkeypatch.setattr(web.system, "admin", lambda *a, **k: (calls.append(a), (True, ""))[1])
    monkeypatch.setattr("visiophone.service.system.admin", lambda *a, **k: (calls.append(a), (True, ""))[1])
    monkeypatch.setattr("visiophone.go2rtc.GO2RTC_CONFIG", str(tmp_path / "go2rtc.yaml"))
    config.update("web", {"password_hash": web.hash_password("secret123")})
    service = Visiophone(config)
    service.hw = hw
    app = web.create_app(service)
    app.config["TESTING"] = True
    c = app.test_client()
    c.calls = calls
    c.service = service
    return c


def csrf(client, path="/"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def login(client):
    return client.post("/login", data={"password": "secret123"})


def test_login_required(client):
    assert client.get("/").status_code == 302
    assert client.get("/api/status").status_code == 401


def test_wrong_password(client):
    assert "Wrong password" in client.post("/login", data={"password": "nope"}, follow_redirects=True).get_data(as_text=True)


@pytest.mark.parametrize("page", ["/", "/camera", "/audio", "/button", "/mqtt", "/system", "/manual", "/api/status"])
def test_pages(client, page):
    login(client)
    assert client.get(page).status_code == 200


def test_csrf_rejected(client):
    login(client)
    assert client.post("/camera", data={"source": "csi"}).status_code == 400


def test_save_camera(client, tmp_path):
    login(client)
    token = csrf(client)
    resp = client.post("/camera", data={"csrf": token, "source": "csi", "csi_index": "0", "resolution": "1280x960",
                                        "framerate": "15", "bitrate_kbps": "2500", "rotation": "0", "noir": "on",
                                        "full_fov": "on"})
    assert resp.status_code == 302
    assert client.service.cfg.section("camera")["noir"] is True
    assert "--mode 1640:1232:10:P" in (tmp_path / "go2rtc.yaml").read_text()
    assert ("restart-go2rtc",) in client.calls


def test_button_same_gpio(client):
    login(client)
    token = csrf(client)
    resp = client.post("/button", data={"csrf": token, "button_enabled": "on", "button_gpio": "17", "pull_up": "up",
                                        "debounce_ms": "50", "min_interval_s": "2", "led_enabled": "on",
                                        "led_gpio": "17", "brightness": "200", "blink_duration_s": "10"},
                       follow_redirects=True)
    assert "same GPIO" in resp.get_data(as_text=True)


def test_mqtt_keeps_password(client):
    login(client)
    client.service.cfg.update("mqtt", {"password": "old"})
    token = csrf(client)
    client.post("/mqtt", data={"csrf": token, "host": "", "port": "1883", "username": "u", "password": "",
                               "device_id": "visiophone", "device_name": "Doorbell", "discovery_prefix": "homeassistant",
                               "status_interval_s": "60"})
    assert client.service.cfg.section("mqtt")["password"] == "old"


def test_password_change(client):
    login(client)
    token = csrf(client)
    client.post("/system/password", data={"csrf": token, "current": "secret123", "new": "newpass99", "confirm": "newpass99"})
    client.post("/logout", data={"csrf": token})
    assert client.post("/login", data={"password": "newpass99"}).status_code == 302


def test_image_settings(client, tmp_path):
    login(client)
    client.service.cfg.update("camera", {"source": "csi"})
    token = csrf(client, "/camera")
    resp = client.post("/camera/image", data={"csrf": token, "brightness": "0.2", "contrast": "1", "saturation": "1.2",
                                              "sharpness": "1", "ev": "0.5", "metering": "average", "awb": "indoor",
                                              "awb_red": "1.5", "awb_blue": "1.5", "denoise": "auto"})
    assert resp.status_code == 302
    text = (tmp_path / "go2rtc.yaml").read_text()
    assert "--brightness 0.2" in text and "--awb indoor" in text and "--metering average" in text
    client.post("/camera/image", data={"csrf": token, "reset": "1"})
    assert "--brightness" not in (tmp_path / "go2rtc.yaml").read_text()


def test_camera_page_with_csi(client):
    login(client)
    client.service.cfg.update("camera", {"source": "csi", "awb": "manual"})
    html = client.get("/camera").get_data(as_text=True)
    assert 'id="image"' in html and 'name="awb_red"' in html and "Manual gains" in html
