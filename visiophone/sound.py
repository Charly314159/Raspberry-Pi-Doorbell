"""Sound level of what the doorbell plays, to drive the LED ring.

Two sources feed the level:
  * the speaker relay (`python3 -m visiophone.sound relay`): go2rtc pipes the
    browser's voice (A-law, 8 kHz) into it; it plays it with aplay and sends
    the level of each chunk to the service over a UNIX datagram socket;
  * a FIFO any player can write to, in raw signed 16-bit mono 8 kHz, for
    example an MPD "fifo" output used for text-to-speech.
"""
import argparse
import logging
import math
import os
import socket
import stat
import struct
import subprocess
import sys
import threading

RUN_DIR = os.environ.get("VISIOPHONE_RUN_DIR", "/run/visiophone")
SOCKET_PATH = os.path.join(RUN_DIR, "sound.sock")
FIFO_PATH = os.path.join(RUN_DIR, "sound.fifo")
RATE = 8000
CHUNK_S = 0.04                      # one level every 40 ms
FLOOR_DB, CEIL_DB = -45.0, -12.0    # mapped to level 0 and 1

log = logging.getLogger(__name__)


def _alaw_to_linear(value):
    value ^= 0x55
    sign, exponent, mantissa = value & 0x80, (value >> 4) & 0x07, value & 0x0F
    sample = (mantissa << 4) + 8
    if exponent:
        sample = (sample + 0x100) << (exponent - 1)
    return sample if sign else -sample


ALAW_TABLE = [_alaw_to_linear(i) for i in range(256)]


def level_from_samples(samples):
    """RMS of 16-bit samples mapped to 0..1 on a dB scale."""
    if not samples:
        return 0.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    if rms < 1:
        return 0.0
    db = 20 * math.log10(rms / 32768)
    return min(1.0, max(0.0, (db - FLOOR_DB) / (CEIL_DB - FLOOR_DB)))


def level_from_alaw(data):
    return level_from_samples([ALAW_TABLE[b] for b in data])


def level_from_s16le(data):
    count = len(data) // 2
    return level_from_samples(struct.unpack(f"<{count}h", data[:count * 2]))


# --- Service side ----------------------------------------------------------

class SoundLevels:
    """Receives levels from the relay socket and the FIFO, calls on_level(0..1)."""

    def __init__(self, on_level):
        self.on_level = on_level
        self._stop = threading.Event()

    def start(self):
        os.makedirs(RUN_DIR, exist_ok=True)
        threading.Thread(target=self._socket_loop, name="sound-socket", daemon=True).start()
        threading.Thread(target=self._fifo_loop, name="sound-fifo", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _socket_loop(self):
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o660)
        sock.settimeout(1)
        while not self._stop.is_set():
            try:
                data = sock.recv(16)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self.on_level(float(data.decode()))
            except ValueError:
                pass

    def _fifo_loop(self):
        try:
            if not stat.S_ISFIFO(os.stat(FIFO_PATH).st_mode):
                os.unlink(FIFO_PATH)
                raise FileNotFoundError
        except FileNotFoundError:
            os.mkfifo(FIFO_PATH)
        # Writable by other users (the mpd user writes into it)
        os.chmod(FIFO_PATH, 0o666)
        chunk = int(RATE * CHUNK_S) * 2
        while not self._stop.is_set():
            try:
                with open(FIFO_PATH, "rb") as fifo:   # blocks until a player opens it
                    while not self._stop.is_set():
                        data = fifo.read(chunk)
                        if not data:
                            break
                        self.on_level(level_from_s16le(data))
                self.on_level(0.0)
            except OSError as e:
                log.warning("Sound FIFO: %s", e)
                self._stop.wait(5)


# --- Speaker relay (run by go2rtc) -----------------------------------------

def relay(device):
    """stdin (A-law 8 kHz from go2rtc) -> aplay, and levels -> service socket."""
    player = subprocess.Popen(["aplay", "-q", "-t", "raw", "-f", "A_LAW", "-r", str(RATE), "-c", "1",
                               "-D", device, "-"], stdin=subprocess.PIPE)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    chunk = int(RATE * CHUNK_S)
    stdin = sys.stdin.buffer
    try:
        while True:
            data = stdin.read1(chunk) if hasattr(stdin, "read1") else stdin.read(chunk)
            if not data:
                break
            player.stdin.write(data)
            player.stdin.flush()
            try:
                sock.sendto(f"{level_from_alaw(data):.3f}".encode(), SOCKET_PATH)
            except OSError:
                pass        # service not running: sound still plays
    except BrokenPipeError:
        pass
    finally:
        try:
            sock.sendto(b"0", SOCKET_PATH)
        except OSError:
            pass
        try:
            player.stdin.close()
        except OSError:
            pass
        player.wait(5)


def main():
    parser = argparse.ArgumentParser(prog="visiophone.sound")
    parser.add_argument("command", choices=["relay"])
    parser.add_argument("--device", required=True, help="ALSA device, e.g. plughw:CARD=Headphones,DEV=0")
    args = parser.parse_args()
    relay(args.device)


if __name__ == "__main__":
    main()
