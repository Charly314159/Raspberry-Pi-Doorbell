"""The Visiophone service: ties together configuration, GPIO, MQTT and go2rtc."""
import collections
import logging
import threading
import time

from . import go2rtc, hardware, system
from .config import Config
from .gpio import DoorbellIO
from .mqtt import MqttBridge
from .sound import SoundLevels

log = logging.getLogger(__name__)


class Visiophone:
    def __init__(self, config=None):
        self.cfg = config or Config()
        self.hw = None
        self.stream_warnings = []
        self.rings = collections.deque(maxlen=20)
        self.io = DoorbellIO(self.on_ring)
        self.io.led_listeners.append(lambda: self.mqtt.publish_led())
        self.mqtt = MqttBridge(self)
        self.sound = SoundLevels(self.on_sound)
        self._voice_at = 0.0          # last time someone spoke through the doorbell
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._apply_lock = threading.Lock()
        self.live_viewers = 0
        self.last_answer = None

    # --- Applying the configuration ----------------------------------------

    def rescan(self):
        self.hw = hardware.scan()
        return self.hw

    def apply_streams(self, restart_always=False):
        """Regenerate go2rtc.yaml; restart go2rtc if it changed. Returns (ok, message)."""
        with self._apply_lock:
            text, self.stream_warnings = go2rtc.render(self.cfg, self.hw or self.rescan())
            try:
                changed = go2rtc.write_if_changed(text)
            except OSError as e:
                return False, f"Cannot write {go2rtc.GO2RTC_CONFIG}: {e}"
            if changed or restart_always:
                log.info("go2rtc configuration %s, restarting go2rtc", "changed" if changed else "unchanged")
                return system.admin("restart-go2rtc")
            return True, "Stream configuration unchanged."

    def apply_io(self):
        self.io.configure(self.cfg.section("button"), self.cfg.section("led"))
        # The LED entity appears or disappears in Home Assistant
        if self.mqtt.connected:
            self.mqtt.publish_discovery()
            self.mqtt.publish_led()

    def apply_mqtt(self):
        self.mqtt.start(self.cfg.section("mqtt"), self.cfg.section("device"))

    # --- Events -------------------------------------------------------------

    def on_ring(self):
        self.rings.appendleft(time.time())
        self.mqtt.publish_ring()

    TALK_HOLD_S = 5

    def on_sound(self, level, source):
        self.io.feed_sound(level)
        # Somebody speaking to the visitor (two-way audio) has answered;
        # voice messages played by the doorbell itself do not count.
        if source == "voice" and level > 0.05:
            was_live = self.live
            self._voice_at = time.monotonic()
            if not was_live:
                self.answer()
                self.mqtt.publish_live_view()

    @property
    def live(self):
        return self.live_viewers > 0 or time.monotonic() - self._voice_at < self.TALK_HOLD_S

    def answer(self):
        """Someone answers the doorbell: stop the LED blinking."""
        self.last_answer = time.time()
        log.info("Doorbell answered")
        self.io.set_led()

    def run_command(self, command):
        if command == "answer":
            self.answer()
            return True, "Answered."
        actions = {
            "restart_stream": ("restart-go2rtc",),
            "reboot": ("reboot",),
            "shutdown": ("poweroff",),
        }
        ok, message = system.admin(*actions[command])
        if not ok:
            log.error("Command %s failed: %s", command, message)
        self.wake_status()
        return ok, message

    def wake_status(self):
        self._wake.set()

    # --- Status -------------------------------------------------------------

    def metrics(self):
        data = system.metrics()
        data["boot_time"] = system.boot_time().isoformat()
        return data

    def _status_loop(self):
        while not self._stop.is_set():
            if self.mqtt.connected:
                try:
                    self.mqtt.publish_system(self.metrics())
                except (OSError, ValueError) as e:
                    log.error("Cannot read the system state: %s", e)
            self._wake.wait(max(5, self.cfg.section("mqtt")["status_interval_s"]))
            self._wake.clear()

    def _live_view_loop(self):
        """Poll go2rtc for browser viewers and publish changes at once."""
        published = False
        while not self._stop.wait(2):
            viewers = go2rtc.live_viewers(go2rtc.api_streams(self.cfg))
            if viewers != self.live_viewers:
                log.info("Live viewers: %d", viewers)
                if viewers and not self.live:
                    self.answer()
                self.live_viewers = viewers
            if self.live != published:
                published = self.live
                self.mqtt.publish_live_view()

    # --- Main ---------------------------------------------------------------

    def start(self):
        """Start everything except the web server."""
        log.info("Scanning hardware")
        self.rescan()
        # Device numbers may have changed since the last boot: refresh go2rtc.yaml
        ok, message = self.apply_streams()
        if not ok:
            log.error("Streams: %s", message)
        for warning in self.stream_warnings:
            log.warning("Streams: %s", warning)
        self.apply_io()
        self.apply_mqtt()
        self.sound.start()
        threading.Thread(target=self._status_loop, name="status", daemon=True).start()
        threading.Thread(target=self._live_view_loop, name="live-view", daemon=True).start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        self.sound.stop()
        self.mqtt.stop()
        self.io.close()
