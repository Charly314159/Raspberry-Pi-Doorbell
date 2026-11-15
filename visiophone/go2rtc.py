"""go2rtc: generation of its configuration and access to its API.

Two streams are produced:
  <name>      camera video (+ microphone): for Home Assistant, Frigate, VLC...
  <name>_web  same stream for browsers: Opus audio, optional H.264 Baseline
              re-encoding, and two-way audio (browser microphone -> speaker).
"""
import base64
import os
import urllib.parse
import urllib.request

import yaml

from . import system
from .config import CONFIG_DIR

GO2RTC_CONFIG = os.path.join(CONFIG_DIR, "go2rtc.yaml")
API_URL = "http://127.0.0.1:1984"
RTSP_PORT = 8554
KILL = "#killsignal=15#killtimeout=5"
# Plays the browser's voice and reports its level to the service (see sound.py)
SPEAKER_RELAY = "/usr/local/bin/visiophone-speaker"
TEST_TONE = "test-tone"     # value of audio.microphone for the built-in test beep


def _local_rtsp(streams_cfg, name):
    auth = ""
    if streams_cfg["username"]:
        auth = (urllib.parse.quote(streams_cfg["username"], safe="") + ":"
                + urllib.parse.quote(streams_cfg["password"], safe="") + "@")
    return f"rtsp://{auth}127.0.0.1:{RTSP_PORT}/{name}"


def pick_sensor_mode(modes, width, height, fps, full_fov):
    """Smallest sensor mode at least as large as the output and fast enough,
    covering the whole sensor if full_fov (other modes crop the image)."""
    candidates = [m for m in modes if m["width"] >= width and m["height"] >= height and m["fps"] >= fps]
    if full_fov:
        candidates = [m for m in candidates if m["full_fov"]]
    if not candidates:
        return None
    return min(candidates, key=lambda m: m["width"] * m["height"])


IMAGE_DEFAULTS = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "sharpness": 1.0, "ev": 0.0,
                  "metering": "centre", "awb": "auto", "denoise": "auto"}
AWB_MODES = ("auto", "incandescent", "tungsten", "fluorescent", "indoor", "daylight", "cloudy", "manual")
METERING_MODES = ("centre", "spot", "average")
DENOISE_MODES = ("auto", "off", "cdn_off", "cdn_fast", "cdn_hq")


def image_args(cam):
    """rpicam options for the image settings that differ from the defaults."""
    args = []
    for key in ("brightness", "contrast", "saturation", "sharpness", "ev"):
        value = float(cam.get(key, IMAGE_DEFAULTS[key]))
        if abs(value - IMAGE_DEFAULTS[key]) > 1e-6:
            args.append(f"--{key} {value:g}")
    if cam.get("metering", "centre") != "centre":
        args.append(f"--metering {cam['metering']}")
    awb = cam.get("awb", "auto")
    if awb == "manual":
        args.append(f"--awbgains {float(cam['awb_red']):g},{float(cam['awb_blue']):g}")
    elif awb != "auto":
        args.append(f"--awb {awb}")
    if cam.get("denoise", "auto") != "auto":
        args.append(f"--denoise {cam['denoise']}")
    return args


def _encoder_args(hw, cam):
    fps = cam["framerate"]
    if hw["hw_encoder"]:
        level = "4.2" if cam["width"] * cam["height"] * fps > 1920 * 1080 * 30 else "4"
        return (f"--codec h264 --profile baseline --level {level} --intra {fps} "
                f"--bitrate {cam['bitrate_kbps'] * 1000}")
    # Pi 5: no hardware encoder, software x264 through libav
    return (f"--codec libav --libav-format h264 --libav-video-codec libx264 "
            f"--libav-video-codec-opts profile=baseline;preset=ultrafast;tune=zerolatency "
            f"--intra {fps} --bitrate {cam['bitrate_kbps'] * 1000}")


def _ffmpeg_h264_args(hw, cam):
    fps = cam["framerate"]
    if hw["hw_encoder"]:
        return (f"-c:v h264_v4l2m2m -profile:v 66 -level 40 -b:v {cam['bitrate_kbps']}k "
                f"-g {fps} -bf 0 -pix_fmt yuv420p")
    return (f"-c:v libx264 -profile:v baseline -preset ultrafast -tune zerolatency "
            f"-b:v {cam['bitrate_kbps']}k -g {fps} -bf 0 -pix_fmt yuv420p")


def video_source(cam, hw, warnings):
    """go2rtc source string for the camera, or None."""
    if cam["source"] == "csi":
        sensor = next((c for c in hw["csi"] if c["index"] == cam["csi_index"]), None)
        if sensor is None:
            warnings.append(f"CSI camera {cam['csi_index']} not found.")
            return None
        args = ["rpicam-vid -t 0 -n --inline --flush", f"--camera {cam['csi_index']}"]
        if cam["noir"]:
            tuning = os.path.join(hw["tuning_dir"], f"{sensor['sensor']}_noir.json")
            if os.path.exists(tuning):
                args.append(f"--tuning-file {tuning}")
            else:
                warnings.append(f"No NoIR tuning file for {sensor['sensor']}: standard tuning used.")
        mode = pick_sensor_mode(sensor["modes"], cam["width"], cam["height"], cam["framerate"], cam["full_fov"])
        if mode:
            args.append(f"--mode {mode['mode']}")
        elif cam["full_fov"] and sensor["modes"]:
            warnings.append("No full field-of-view sensor mode fits this resolution and frame rate.")
        if cam["rotation"] == 180:
            args.append("--rotation 180")
        if cam["hflip"]:
            args.append("--hflip")
        if cam["vflip"]:
            args.append("--vflip")
        args.extend(image_args(cam))
        args.append(f"--width {cam['width']} --height {cam['height']} --framerate {cam['framerate']}")
        args.append(_encoder_args(hw, cam))
        args.append("-o -")
        return "exec:" + " ".join(args) + KILL

    if cam["source"] == "usb":
        path = cam["usb_device"]
        usb = next((c for c in hw["usb"] if c["path"] == path), None)
        if usb is None:
            warnings.append(f"USB camera {path or '(none selected)'} not found.")
            return None
        w, h, fps = cam["width"], cam["height"], cam["framerate"]
        if cam["usb_format"] == "h264":
            if "h264" not in usb["formats"]:
                warnings.append("This USB camera has no H.264 output.")
                return None
            # v4l2-ctl rather than go2rtc's v4l2 source: it keeps SPS/PPS in the stream
            return (f"exec:v4l2-ctl -d {path} --set-fmt-video=width={w},height={h},pixelformat=H264 "
                    f"--set-parm={fps} --stream-mmap --stream-to=- {KILL}")
        input_format = {"mjpeg": "mjpeg", "yuyv": "yuyv422"}[cam["usb_format"]]
        filters = []
        if cam["rotation"] == 180:
            filters.append("hflip,vflip")
        else:
            if cam["hflip"]:
                filters.append("hflip")
            if cam["vflip"]:
                filters.append("vflip")
        vf = f"-vf {','.join(filters)} " if filters else ""
        return (f"exec:ffmpeg -hide_banner -v error -f v4l2 -input_format {input_format} "
                f"-video_size {w}x{h} -framerate {fps} -i {path} {vf}{_ffmpeg_h264_args(hw, cam)} "
                f"-an -f rtsp {{output}}{KILL}")
    return None


def build(cfg, hw):
    """(go2rtc config as a dict, list of warnings)"""
    warnings = []
    cam, audio, streams_cfg = cfg.section("camera"), cfg.section("audio"), cfg.section("streams")
    name = streams_cfg["name"]
    streams = {}

    video = video_source(cam, hw, warnings)
    mic = speaker = None
    if audio["microphone"] == TEST_TONE:
        mic = TEST_TONE
    elif audio["microphone"]:
        mic = system.alsa_path_or_warn(audio["microphone"], "capture", warnings)
    if audio["speaker"]:
        speaker = system.alsa_path_or_warn(audio["speaker"], "playback", warnings)

    main = []
    if video:
        main.append(video)
    if mic == TEST_TONE:
        # A beep every second instead of a microphone, to test the doorbell -> viewer audio path
        main.append("exec:ffmpeg -hide_banner -v error -re -f lavfi -i sine=frequency=880:beep_factor=4 "
                    "-c:a pcm_alaw -ar 8000 -ac 1 -f rtsp {output}" + KILL)
    elif mic:
        main.append(f"alsa:device?audio={mic}")
    if main:
        streams[name] = main

        web = []
        transcode = cam["web_transcode"] and video
        if transcode or mic:
            # Hardware decoding of the input when re-encoding (Pi 1-4)
            decoder = "-c:v h264_v4l2m2m " if transcode and hw["hw_encoder"] else ""
            if not video:
                video_args = "-vn"
            elif transcode:
                video_args = _ffmpeg_h264_args(hw, cam)
            else:
                video_args = "-c:v copy"
            audio_args = "-c:a libopus -ar 48000 -ac 1 -b:a 32k" if mic else "-an"
            web.append(
                f"exec:ffmpeg -hide_banner -v error -rtsp_transport tcp {decoder}"
                f"-i {_local_rtsp(streams_cfg, name)} {video_args} {audio_args} "
                f"-rtsp_transport tcp -f rtsp {{output}}{KILL}"
            )
        else:
            web.append(_local_rtsp(streams_cfg, name))
        if speaker:
            if audio.get("speaker_relay", True):
                card_id, dev = audio["speaker"].rsplit(",", 1)
                # Browsers send Opus, G722, PCMU or PCMA: ask go2rtc for A-law 8 kHz
                # (its exec default, 16 kHz PCM, matches no browser and never starts)
                relay = (f"exec:{SPEAKER_RELAY} --device plughw:CARD={card_id},DEV={dev}"
                         f"#backchannel=1#audio=pcma/8000")
            else:
                relay = f"alsa:device?audio={speaker}"
            web.append(relay)
            # Speaker only, for another go2rtc (e.g. Frigate's) that adds two-way
            # audio to its own copy of the stream: webrtc:ws://<pi>:1984/api/ws?src=<name>_talk
            streams[f"{name}_talk"] = [relay]
        streams[f"{name}_web"] = web
    else:
        warnings.append("No camera and no microphone configured: no stream is published.")

    config = {
        "streams": streams or {},
        "api": {"listen": ":1984"},
        "rtsp": {"listen": f":{RTSP_PORT}"},
        "webrtc": {"listen": ":8555"},
    }
    if streams_cfg.get("cross_origin"):
        config["api"]["origin"] = "*"
    if streams_cfg["username"]:
        for section in ("api", "rtsp"):
            config[section]["username"] = streams_cfg["username"]
            config[section]["password"] = streams_cfg["password"]
    return config, warnings


def render(cfg, hw):
    config, warnings = build(cfg, hw)
    text = ("# Generated by Visiophone from config.yaml: do not edit, changes are overwritten.\n"
            + yaml.safe_dump(config, sort_keys=False, width=1000, allow_unicode=True))
    return text, warnings


def write_if_changed(text, path=None):
    path = path or GO2RTC_CONFIG
    try:
        with open(path) as f:
            if f.read() == text:
                return False
    except FileNotFoundError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o640)
    os.replace(tmp, path)
    return True


def _request(cfg, path, timeout=5):
    streams_cfg = cfg.section("streams")
    req = urllib.request.Request(API_URL + path)
    if streams_cfg["username"]:
        token = base64.b64encode(f"{streams_cfg['username']}:{streams_cfg['password']}".encode()).decode()
        req.add_header("Authorization", "Basic " + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def api_streams(cfg):
    import json
    try:
        return json.loads(_request(cfg, "/api/streams"))
    except (OSError, ValueError):
        return None


# Consumers that are people watching (browsers), as opposed to RTSP clients
# such as Home Assistant recordings, Frigate or the internal _web stream.
LIVE_FORMATS = ("webrtc", "mse", "hls", "mjpeg")


def live_viewers(streams):
    """Number of browser viewers over all streams, from /api/streams.
    The *_talk streams are left out: another go2rtc stays connected to them."""
    count = 0
    for name, stream in (streams or {}).items():
        if name.endswith("_talk"):
            continue
        for consumer in stream.get("consumers") or []:
            if str(consumer.get("format_name", "")).startswith(LIVE_FORMATS):
                count += 1
    return count


def snapshot(cfg):
    """JPEG of the current frame (go2rtc decodes a keyframe with ffmpeg)."""
    name = cfg.section("streams")["name"]
    return _request(cfg, f"/api/frame.jpeg?src={urllib.parse.quote(name)}", timeout=15)
