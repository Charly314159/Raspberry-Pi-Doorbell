"""Doorbell push button and its LED ring, through gpiozero."""
import logging
import threading
import time

log = logging.getLogger(__name__)

BLINK_PERIOD_S = 0.5
# LED effects, selectable from Home Assistant: (on_time, off_time) for blinks,
# (fade in, fade out) for breathing. Every effect peaks at the chosen brightness.
EFFECTS = {
    "none": None,
    "blink": ("blink", 0.5, 0.5),
    "fast_blink": ("blink", 0.15, 0.15),
    "breathe": ("pulse", 1.2, 1.2),
}


class DoorbellIO:
    def __init__(self, on_ring):
        self.on_ring = on_ring
        self._lock = threading.RLock()
        self._button = None
        self._led = None
        self._restore_timer = None
        self._anim_stop = None
        self._anim_thread = None
        self.button_cfg = {}
        self.led_cfg = {}
        self.button_error = None
        self.led_error = None
        self.last_press = None        # epoch of the last accepted press
        self.press_count = 0
        self._last_accepted = 0.0
        self.led_on = False
        self.led_brightness = 255
        self.led_effect = "none"
        self.led_listeners = []       # callables notified when the LED state changes

    # --- Configuration ------------------------------------------------------

    def configure(self, button_cfg, led_cfg):
        with self._lock:
            self.close()
            self.button_cfg, self.led_cfg = button_cfg, led_cfg
            self.button_error = self.led_error = None
            if button_cfg["enabled"]:
                try:
                    from gpiozero import Button
                    self._button = Button(button_cfg["gpio"], pull_up=button_cfg["pull_up"],
                                          bounce_time=max(button_cfg["debounce_ms"], 1) / 1000)
                    self._button.when_pressed = self._pressed
                    log.info("Button on GPIO%d", button_cfg["gpio"])
                except Exception as e:  # GPIO busy, invalid pin, no GPIO library...
                    self.button_error = str(e)
                    log.error("Button on GPIO%s unavailable: %s", button_cfg["gpio"], e)
            if led_cfg["enabled"]:
                try:
                    from gpiozero import PWMLED
                    self._led = PWMLED(led_cfg["gpio"])
                    self.led_brightness = led_cfg["brightness"]
                    self._set_led(led_cfg["on_at_start"], led_cfg["brightness"])
                    log.info("LED on GPIO%d", led_cfg["gpio"])
                except Exception as e:
                    self.led_error = str(e)
                    log.error("LED on GPIO%s unavailable: %s", led_cfg["gpio"], e)

    def close(self):
        with self._lock:
            if self._restore_timer:
                self._restore_timer.cancel()
                self._restore_timer = None
            self._stop_animation()
            for device in (self._button, self._led):
                if device is not None:
                    try:
                        device.close()
                    except Exception:
                        pass
            self._button = self._led = None

    # --- Button -------------------------------------------------------------

    def _pressed(self):
        now = time.monotonic()
        if now - self._last_accepted < self.button_cfg.get("min_interval_s", 0):
            return
        self._last_accepted = now
        self.last_press = time.time()
        self.press_count += 1
        log.info("Doorbell button pressed")
        if self._led is not None and self.led_cfg.get("blink_on_ring"):
            self.blink(self.led_cfg["blink_duration_s"])
        try:
            self.on_ring()
        except Exception:
            log.exception("Ring handler failed")

    @property
    def is_pressed(self):
        button = self._button
        return bool(button and button.is_pressed)

    # --- LED ----------------------------------------------------------------

    # gpiozero's own blink() and pulse() always go up to full power: the
    # animations below are ours, so that they peak at the chosen brightness.

    def _stop_animation(self):
        if self._anim_stop is not None:
            self._anim_stop.set()
        if self._anim_thread is not None and self._anim_thread is not threading.current_thread():
            self._anim_thread.join(1)
        self._anim_stop = self._anim_thread = None

    def _animate(self, kind, first, second, cycles=None):
        """Run an effect in the background: kind 'blink' (on/off times) or
        'pulse' (fade in/out times), forever or for a number of cycles."""
        self._stop_animation()
        led = self._led
        if led is None:
            return
        stop = threading.Event()

        def run():
            done = 0
            while not stop.is_set() and (cycles is None or done < cycles):
                peak = self.led_brightness / 255     # read each cycle: follows brightness changes
                try:
                    if kind == "blink":
                        led.value = peak
                        if stop.wait(first):
                            return
                        led.value = 0
                        if stop.wait(second):
                            return
                    else:
                        for fade, levels in ((first, range(1, 41)), (second, range(39, -1, -1))):
                            for level in levels:
                                # Quadratic curve: looks even to the eye, unlike a linear ramp
                                led.value = peak * (level / 40) ** 2
                                if stop.wait(fade / 40):
                                    return
                except Exception:   # LED closed while animating
                    return
                done += 1

        self._anim_stop = stop
        self._anim_thread = threading.Thread(target=run, name="led-effect", daemon=True)
        self._anim_thread.start()

    def _apply_led(self):
        """Drive the LED from the steady state: off, on, or an effect."""
        led = self._led
        if led is None:
            return
        effect = EFFECTS.get(self.led_effect) if self.led_on else None
        if effect is None:
            self._stop_animation()
            led.value = self.led_brightness / 255 if self.led_on else 0
        else:
            self._animate(*effect)

    def _notify(self):
        for listener in list(self.led_listeners):
            try:
                listener()
            except Exception:
                log.exception("LED listener failed")

    def _set_led(self, on, brightness, effect=None):
        self.led_on, self.led_brightness = bool(on), max(1, min(255, int(brightness)))
        if effect is not None:
            self.led_effect = effect if effect in EFFECTS else "none"
        self._apply_led()
        self._notify()

    def set_led(self, on=None, brightness=None, effect=None):
        """Steady LED state (from Home Assistant or the web interface).
        Choosing an effect turns the LED on."""
        with self._lock:
            if self._restore_timer:
                self._restore_timer.cancel()
                self._restore_timer = None
            if effect is not None and on is None:
                on = True
            self._set_led(self.led_on if on is None else on,
                          self.led_brightness if brightness is None else brightness, effect)

    def blink(self, duration_s):
        """Blink for duration_s, then come back to the steady state."""
        with self._lock:
            led = self._led
            if led is None:
                return
            if self._restore_timer:
                self._restore_timer.cancel()
            half = BLINK_PERIOD_S / 2
            self._animate("blink", half, half, cycles=max(1, int(duration_s / BLINK_PERIOD_S)))
            self._restore_timer = threading.Timer(duration_s, self._restore)
            self._restore_timer.daemon = True
            self._restore_timer.start()

    def _restore(self):
        with self._lock:
            self._restore_timer = None
            self._apply_led()

    def led_state(self):
        return {"enabled": self._led is not None, "on": self.led_on, "brightness": self.led_brightness,
                "effect": self.led_effect}
