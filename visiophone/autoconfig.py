"""First configuration from the detected hardware.

Run once by install.sh when there is no configuration yet. It only picks
sensible devices; everything can be changed later in the web interface.
"""
from . import hardware


def _best_usb_format(camera):
    for fmt in ("h264", "mjpeg", "yuyv"):
        if fmt in camera["formats"]:
            return fmt
    return None


def _best_size(sizes, wanted=((1280, 960), (1280, 720), (1920, 1080), (640, 480))):
    available = {tuple(int(v) for v in size.split("x")) for size in sizes}
    for size in wanted:
        if size in available:
            return size
    return max(available) if available else (1280, 720)


def propose(hw):
    """Configuration sections (dict) suited to the hardware found."""
    camera = {}
    if hw["csi"]:
        cam = hw["csi"][0]
        camera = {"source": "csi", "csi_index": cam["index"], "width": 1280, "height": 960, "framerate": 15}
        # 4:3 keeps the whole field of view of most sensors; 16:9 sensors get 1280x720
        if cam["width"] * 3 != cam["height"] * 4 and abs(cam["width"] / cam["height"] - 16 / 9) < 0.1:
            camera.update(width=1280, height=720)
    elif hw["usb"]:
        cam = hw["usb"][0]
        fmt = _best_usb_format(cam)
        if fmt:
            w, h = _best_size(cam["formats"][fmt])
            camera = {"source": "usb", "usb_device": cam["path"], "usb_format": fmt,
                      "width": w, "height": h, "framerate": 15 if fmt != "yuyv" else 10}

    # Prefer a sound card that has both a microphone and a speaker (USB audio
    # adapter): same clock for both directions, needed for echo cancellation
    audio = {}
    cards = hardware.sound_cards()
    duplex = [c for c in cards if c["capture"] and c["playback"]]
    if duplex:
        card = duplex[0]
        audio = {"microphone": f"{card['id']},{card['capture'][0]}",
                 "speaker": f"{card['id']},{card['playback'][0]}"}
    else:
        if hw["capture"]:
            audio["microphone"] = hw["capture"][0]["key"]
        if hw["playback"]:
            audio["speaker"] = hw["playback"][0]["key"]
    return {"camera": camera, "audio": audio}


def apply(cfg, hw=None):
    hw = hw or hardware.scan()
    proposal = propose(hw)
    for section, values in proposal.items():
        if values:
            cfg.update(section, values)
    return proposal
