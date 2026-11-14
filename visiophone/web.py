"""Web interface: status, configuration, system actions and user manual."""
import functools
import hmac
import os
import secrets
import time

from flask import (Flask, Response, abort, flash, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import __version__, go2rtc, hardware, system
from .config import CONFIG_DIR

DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
SECRET_KEY_PATH = os.path.join(CONFIG_DIR, "secret_key")
RESOLUTIONS = [(640, 480), (1280, 720), (1280, 960), (1920, 1080)]
FRAMERATES = [5, 10, 15, 20, 25, 30]


def _secret_key():
    try:
        with open(SECRET_KEY_PATH, "rb") as f:
            key = f.read()
            if len(key) >= 32:
                return key
    except FileNotFoundError:
        pass
    key = secrets.token_bytes(32)
    os.makedirs(os.path.dirname(SECRET_KEY_PATH), exist_ok=True)
    fd = os.open(SECRET_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def hash_password(password):
    return generate_password_hash(password)


def create_app(service):
    app = Flask(__name__)
    app.secret_key = _secret_key()
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PERMANENT_SESSION_LIFETIME=30 * 24 * 3600)
    cfg = service.cfg
    failed_logins = {}

    # --- Helpers -------------------------------------------------------------

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    @app.context_processor
    def inject():
        return {"csrf_token": csrf_token, "version": __version__, "device_name": cfg.section("device")["name"],
                "gpio_header": hardware.GPIO_HEADER, "gpio_notes": hardware.GPIO_NOTES}

    @app.before_request
    def check_csrf():
        if request.method == "POST" and request.endpoint != "login":
            token = request.form.get("csrf") or request.headers.get("X-CSRF-Token", "")
            expected = session.get("csrf", "")
            if not expected or not hmac.compare_digest(token, expected):
                abort(400, "Invalid form token, reload the page.")

    def login_required(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            if not session.get("user"):
                if request.path.startswith("/api/"):
                    abort(401)
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapper

    def form_int(name, low, high, default=None):
        try:
            value = int(request.form.get(name, default))
        except (TypeError, ValueError):
            raise ValueError(f"{name}: a whole number is expected")
        if not low <= value <= high:
            raise ValueError(f"{name}: must be between {low} and {high}")
        return value

    def form_float(name, low, high):
        try:
            value = float(request.form.get(name))
        except (TypeError, ValueError):
            raise ValueError(f"{name}: a number is expected")
        if not low <= value <= high:
            raise ValueError(f"{name}: must be between {low} and {high}")
        return value

    def checked(name):
        return request.form.get(name) == "on"

    def report(ok_message, result):
        ok, message = result
        if ok:
            flash(ok_message, "ok")
        else:
            flash(message, "error")
        for warning in service.stream_warnings:
            flash(warning, "warning")

    # --- Authentication ------------------------------------------------------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        password_hash = cfg.section("web")["password_hash"]
        if request.method == "POST":
            ip = request.remote_addr
            failures = [t for t in failed_logins.get(ip, []) if t > time.time() - 300]
            if len(failures) >= 10:
                flash("Too many failed attempts, try again in a few minutes.", "error")
                return render_template("login.html"), 429
            if password_hash and check_password_hash(password_hash, request.form.get("password", "")):
                failed_logins.pop(ip, None)
                session.clear()
                session["user"] = "admin"
                session.permanent = True
                target = request.args.get("next", "/")
                return redirect(target if target.startswith("/") and not target.startswith("//") else "/")
            failed_logins[ip] = failures + [time.time()]
            time.sleep(1)
            flash("Wrong password." if password_hash else
                  "No password set yet: run 'sudo visiophone-passwd' on the Pi.", "error")
        return render_template("login.html", no_password=not password_hash)

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # --- Status --------------------------------------------------------------

    def status_data():
        metrics = service.metrics()
        streams = go2rtc.api_streams(cfg)
        name = cfg.section("streams")["name"]
        stream = (streams or {}).get(name) or {}
        last = service.io.last_press
        return {
            "metrics": metrics,
            "go2rtc_api": streams is not None,
            "stream_defined": name in (streams or {}),
            "stream_clients": len(stream.get("consumers") or []),
            "live_viewers": service.live_viewers,
            "stream_active": bool(stream.get("producers") and any(p.get("state") or p.get("medias")
                                                                  for p in stream["producers"])),
            "mqtt": {"enabled": cfg.section("mqtt")["enabled"], "connected": service.mqtt.connected,
                     "error": service.mqtt.last_error},
            "button": {"error": service.io.button_error, "pressed": service.io.is_pressed,
                       "count": service.io.press_count, "last": last},
            "led": dict(service.io.led_state(), error=service.io.led_error),
            "rings": list(service.rings)[:10],
            "warnings": service.stream_warnings,
        }

    @app.route("/")
    @login_required
    def index():
        return render_template("status.html", status=status_data(), streams=cfg.section("streams"),
                               ip=system.ip_address(), model=hardware.pi_model())

    @app.route("/api/status")
    @login_required
    def api_status():
        return jsonify(status_data())

    # --- Camera --------------------------------------------------------------

    @app.route("/camera", methods=["GET", "POST"])
    @login_required
    def camera():
        if request.method == "POST":
            try:
                source = request.form.get("source", "none")
                if source not in ("none", "csi", "usb"):
                    raise ValueError("Unknown camera source")
                values = {"source": source}
                if source == "csi":
                    values["csi_index"] = form_int("csi_index", 0, 7, 0)
                elif source == "usb":
                    values["usb_device"] = request.form.get("usb_device", "")
                    usb_format = request.form.get("usb_format", "h264")
                    if usb_format not in ("h264", "mjpeg", "yuyv"):
                        raise ValueError("Unknown USB format")
                    values["usb_format"] = usb_format
                width, height = (int(v) for v in request.form.get("resolution", "1280x720").split("x"))
                if not (160 <= width <= 1920 and 120 <= height <= 1088):
                    raise ValueError("Resolution out of range (1920x1080 at most)")
                values.update(
                    width=width, height=height,
                    framerate=form_int("framerate", 1, 60),
                    bitrate_kbps=form_int("bitrate_kbps", 200, 10000),
                    rotation=180 if request.form.get("rotation") == "180" else 0,
                    hflip=checked("hflip"), vflip=checked("vflip"), noir=checked("noir"),
                    full_fov=checked("full_fov"), web_transcode=checked("web_transcode"),
                )
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("camera"))
            cfg.update("camera", values)
            report("Camera settings saved, stream restarted.", service.apply_streams())
            return redirect(url_for("camera"))
        return render_template("camera.html", cam=cfg.section("camera"), hw=service.hw or service.rescan(),
                               sensors=hardware.KNOWN_SENSORS, resolutions=RESOLUTIONS, framerates=FRAMERATES,
                               awb_modes=go2rtc.AWB_MODES, metering_modes=go2rtc.METERING_MODES,
                               denoise_modes=go2rtc.DENOISE_MODES,
                               streams=cfg.section("streams"))

    @app.route("/camera/image", methods=["POST"])
    @login_required
    def camera_image():
        if request.form.get("reset"):
            values = dict(go2rtc.IMAGE_DEFAULTS, awb_red=1.5, awb_blue=1.5)
        else:
            try:
                values = {
                    "brightness": form_float("brightness", -1, 1),
                    "contrast": form_float("contrast", 0, 2),
                    "saturation": form_float("saturation", 0, 2),
                    "sharpness": form_float("sharpness", 0, 2),
                    "ev": form_float("ev", -4, 4),
                    "awb_red": form_float("awb_red", 0.5, 4),
                    "awb_blue": form_float("awb_blue", 0.5, 4),
                }
                for key, allowed in (("metering", go2rtc.METERING_MODES), ("awb", go2rtc.AWB_MODES),
                                     ("denoise", go2rtc.DENOISE_MODES)):
                    value = request.form.get(key, "")
                    if value not in allowed:
                        raise ValueError(f"{key}: unknown value")
                    values[key] = value
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("camera") + "#image")
        cfg.update("camera", values)
        report("Image settings applied.", service.apply_streams())
        return redirect(url_for("camera") + "#image")

    @app.route("/camera/rescan", methods=["POST"])
    @login_required
    def camera_rescan():
        service.rescan()
        flash("Hardware scanned again.", "ok")
        return redirect(request.referrer or url_for("camera"))

    @app.route("/camera/overlay", methods=["POST"])
    @login_required
    def camera_overlay():
        sensor = request.form.get("sensor", "auto")
        port = request.form.get("port", "")
        if sensor != "auto" and sensor not in hardware.KNOWN_SENSORS or port not in ("", "cam0", "cam1"):
            abort(400)
        ok, message = system.admin("camera-overlay", sensor, *( [port] if port else []))
        if not ok:
            flash(message, "error")
            return redirect(url_for("camera"))
        if checked("reboot"):
            system.admin("reboot")
            return render_template("rebooting.html")
        flash("Boot configuration updated. Reboot the Pi to apply it.", "ok")
        return redirect(url_for("camera"))

    @app.route("/camera/snapshot.jpg")
    @login_required
    def snapshot():
        try:
            return Response(go2rtc.snapshot(cfg), mimetype="image/jpeg", headers={"Cache-Control": "no-store"})
        except OSError:
            abort(503)

    # --- Audio ---------------------------------------------------------------

    @app.route("/audio", methods=["GET", "POST"])
    @login_required
    def audio():
        if request.method == "POST":
            capture = {d["key"] for d in hardware.audio_devices("capture")}
            playback = {d["key"] for d in hardware.audio_devices("playback")}
            mic, speaker = request.form.get("microphone", ""), request.form.get("speaker", "")
            current = cfg.section("audio")
            # A device that is unplugged right now may stay selected
            if mic and mic not in capture and mic != current["microphone"] or \
               speaker and speaker not in playback and speaker != current["speaker"]:
                abort(400)
            cfg.update("audio", {"microphone": mic, "speaker": speaker})
            for kind, key in (("playback", speaker), ("capture", mic)):
                card = hardware.card_index(key)
                field = f"{kind}_volume"
                if card is not None and request.form.get(field):
                    hardware.set_volume(card, kind, form_int(field, 0, 100))
            system.admin("alsa-store")
            report("Audio settings saved.", service.apply_streams())
            return redirect(url_for("audio"))
        audio_cfg = cfg.section("audio")
        volumes = {}
        for kind, key in (("playback", audio_cfg["speaker"]), ("capture", audio_cfg["microphone"])):
            card = hardware.card_index(key)
            volumes[kind] = hardware.get_volume(card, kind) if card is not None else None
        return render_template("audio.html", audio=audio_cfg, capture=hardware.audio_devices("capture"),
                               playback=hardware.audio_devices("playback"), volumes=volumes,
                               streams=cfg.section("streams"))

    @app.route("/audio/test", methods=["POST"])
    @login_required
    def audio_test():
        speaker = hardware.alsa_pcm_path(cfg.section("audio")["speaker"], "playback")
        if not speaker:
            return jsonify(ok=False, message="No speaker selected."), 400
        key = cfg.section("audio")["speaker"]
        card_id, dev = key.rsplit(",", 1)
        ok, message = system.play_test_tone(f"plughw:CARD={card_id},DEV={dev}")
        return jsonify(ok=ok, message=message or "Test tone played.")

    # --- Button and LED ------------------------------------------------------

    @app.route("/button", methods=["GET", "POST"])
    @login_required
    def button():
        if request.method == "POST":
            try:
                button_values = {
                    "enabled": checked("button_enabled"),
                    "gpio": form_int("button_gpio", 2, 27),
                    "pull_up": request.form.get("pull_up") != "down",
                    "debounce_ms": form_int("debounce_ms", 0, 1000),
                    "min_interval_s": form_float("min_interval_s", 0, 60),
                }
                led_values = {
                    "enabled": checked("led_enabled"),
                    "gpio": form_int("led_gpio", 2, 27),
                    "on_at_start": checked("on_at_start"),
                    "brightness": form_int("brightness", 1, 255),
                    "blink_on_ring": checked("blink_on_ring"),
                    "blink_duration_s": form_int("blink_duration_s", 1, 120),
                }
                if button_values["enabled"] and led_values["enabled"] and \
                        button_values["gpio"] == led_values["gpio"]:
                    raise ValueError("The button and the LED cannot use the same GPIO.")
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("button"))
            cfg.update("button", button_values)
            cfg.update("led", led_values)
            service.apply_io()
            for error in (service.io.button_error, service.io.led_error):
                if error:
                    flash(error, "error")
            flash("Button and LED settings saved.", "ok")
            return redirect(url_for("button"))
        return render_template("button.html", button=cfg.section("button"), led=cfg.section("led"))

    @app.route("/api/led", methods=["POST"])
    @login_required
    def api_led():
        action = request.form.get("action")
        if action == "blink":
            service.io.blink(cfg.section("led")["blink_duration_s"])
        elif action in ("on", "off"):
            service.io.set_led(on=action == "on", effect="none" if action == "on" else None)
        elif action == "breathe":
            service.io.set_led(effect="breathe")
        elif action == "ring":
            service.io._pressed()
        else:
            abort(400)
        return jsonify(service.io.led_state())

    # --- MQTT / Home Assistant ----------------------------------------------

    @app.route("/mqtt", methods=["GET", "POST"])
    @login_required
    def mqtt_page():
        if request.method == "POST":
            current = cfg.section("mqtt")
            try:
                device_id = request.form.get("device_id", "").strip()
                if not device_id.replace("_", "").isalnum() or not device_id.isascii():
                    raise ValueError("Device ID: letters, digits and _ only")
                values = {
                    "enabled": checked("enabled"),
                    "host": request.form.get("host", "").strip(),
                    "port": form_int("port", 1, 65535),
                    "username": request.form.get("username", "").strip(),
                    "discovery_prefix": request.form.get("discovery_prefix", "homeassistant").strip() or "homeassistant",
                    "status_interval_s": form_int("status_interval_s", 10, 3600),
                }
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("mqtt_page"))
            # An empty password field keeps the stored password
            password = request.form.get("password", "")
            values["password"] = "" if checked("clear_password") else (password or current["password"])
            cfg.update("mqtt", values)
            cfg.update("device", {"name": request.form.get("device_name", "").strip() or "Doorbell",
                                  "id": device_id})
            service.apply_mqtt()
            flash("MQTT settings saved. Connecting…" if values["enabled"] else "MQTT disabled.", "ok")
            return redirect(url_for("mqtt_page"))
        return render_template("mqtt.html", mqtt=cfg.section("mqtt"), device=cfg.section("device"),
                               connected=service.mqtt.connected, error=service.mqtt.last_error)

    # --- System --------------------------------------------------------------

    @app.route("/system")
    @login_required
    def system_page():
        return render_template("system.html", streams=cfg.section("streams"), model=hardware.pi_model(),
                               ip=system.ip_address(), hw=service.hw or service.rescan())

    @app.route("/system/streams", methods=["POST"])
    @login_required
    def system_streams():
        name = request.form.get("name", "").strip()
        if not name or not name.replace("_", "").replace("-", "").isalnum() or not name.isascii():
            flash("Stream name: letters, digits, - and _ only.", "error")
            return redirect(url_for("system_page"))
        current = cfg.section("streams")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "") or current["password"]
        if not username:
            password = ""
        elif not password:
            flash("A password is needed with a user name.", "error")
            return redirect(url_for("system_page"))
        cfg.update("streams", {"name": name, "username": username, "password": password,
                               "cross_origin": checked("cross_origin")})
        report("Stream settings saved.", service.apply_streams())
        return redirect(url_for("system_page"))

    @app.route("/system/password", methods=["POST"])
    @login_required
    def system_password():
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        if not check_password_hash(cfg.section("web")["password_hash"], current):
            flash("Current password is wrong.", "error")
        elif len(new) < 8 or new != request.form.get("confirm"):
            flash("The new password must have at least 8 characters and match its confirmation.", "error")
        else:
            cfg.update("web", {"password_hash": hash_password(new)})
            flash("Password changed.", "ok")
        return redirect(url_for("system_page"))

    @app.route("/system/action", methods=["POST"])
    @login_required
    def system_action():
        action = request.form.get("action")
        if action == "restart_stream":
            report("Video stream restarted.", service.apply_streams(restart_always=True))
            return redirect(request.referrer or url_for("system_page"))
        if action in ("reboot", "shutdown"):
            ok, message = service.run_command(action)
            if ok:
                return render_template("rebooting.html", shutdown=action == "shutdown")
            flash(message, "error")
            return redirect(url_for("system_page"))
        abort(400)

    @app.route("/system/logs")
    @login_required
    def system_logs():
        return Response(system.logs(), mimetype="text/plain; charset=utf-8")

    # --- Manual --------------------------------------------------------------

    @app.route("/manual")
    @login_required
    def manual():
        import markdown
        try:
            with open(os.path.join(DOCS_DIR, "MANUAL.md"), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            text = "# Manual\n\nThe manual file is missing from this installation."
        md = markdown.Markdown(extensions=["tables", "toc", "fenced_code"])
        html = md.convert(text)
        return render_template("manual.html", content=html, toc=md.toc)

    return app
