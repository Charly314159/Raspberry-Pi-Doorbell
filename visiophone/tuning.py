"""Colour calibration of CSI cameras through a libcamera tuning file.

libcamera applies a colour correction matrix (CCM) chosen by the colour
temperature of the scene. A calibration matrix M, measured by comparing the
doorbell's picture with a reference (another camera, a colour chart), is
applied on top of every CCM of the base tuning file: CCM' = M x CCM. The
result is written to the state directory and passed to rpicam-vid.
"""
import hashlib
import json
import os

from .config import STATE_DIR


def parse_matrix(text):
    """'a, b, c, d, e, f, g, h, i' (9 numbers, row by row) -> list of 9 floats."""
    values = [float(v) for v in text.replace(";", ",").replace("\n", ",").split(",") if v.strip()]
    if len(values) != 9:
        raise ValueError("The colour matrix needs 9 numbers, row by row")
    if any(abs(v) > 8 for v in values):
        raise ValueError("Colour matrix values must stay between -8 and 8")
    return values


def _multiply(m, c):
    return [sum(m[3 * r + k] * c[3 * k + col] for k in range(3)) for r in range(3) for col in range(3)]


def calibrated(base_path, matrix):
    """Tuning data of base_path with every CCM multiplied by matrix."""
    with open(base_path) as f:
        data = json.load(f)
    found = False
    for algorithm in data.get("algorithms", []):
        ccm = algorithm.get("rpi.ccm")
        if ccm:
            for entry in ccm.get("ccms", []):
                entry["ccm"] = [round(v, 5) for v in _multiply(matrix, entry["ccm"])]
                found = True
    if not found:
        raise ValueError(f"{base_path} has no colour matrix (rpi.ccm) to calibrate")
    return data


def write_calibrated(base_path, matrix, directory=None):
    """Write the calibrated tuning file, return its path. The name contains a
    hash of its content, so a new matrix changes the go2rtc configuration and
    restarts the camera."""
    directory = directory or STATE_DIR
    text = json.dumps(calibrated(base_path, matrix), indent=1)
    digest = hashlib.sha1(text.encode()).hexdigest()[:8]
    name = os.path.splitext(os.path.basename(base_path))[0]
    path = os.path.join(directory, f"tuning-{name}-{digest}.json")
    if not os.path.exists(path):
        os.makedirs(directory, exist_ok=True)
        for old in os.listdir(directory):          # keep only the current file
            if old.startswith(f"tuning-{name}-") and old.endswith(".json"):
                os.unlink(os.path.join(directory, old))
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    return path
