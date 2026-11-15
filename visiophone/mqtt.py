"""MQTT link to Home Assistant, with MQTT discovery.

Home Assistant creates the device and its entities by itself: doorbell event,
Pi diagnostics, restart/shutdown buttons and, if enabled, the LED ring light.
"""
import json
import logging
import threading

import paho.mqtt.client as mqtt

from . import __version__, hardware
from .gpio import EFFECTS

log = logging.getLogger(__name__)

COMMANDS = ("answer", "restart_stream", "reboot", "shutdown")


class MqttBridge:
    def __init__(self, service):
        self.service = service
        self._client = None
        self._lock = threading.RLock()
        self.cfg = {}
        self.device_cfg = {"name": "Doorbell", "id": "visiophone"}
        self.base = "visiophone"
        self.last_error = None
        self._published_led = None

    # --- Topics -------------------------------------------------------------

    def topic(self, suffix):
        return f"{self.base}/{suffix}"

    @property
    def connected(self):
        client = self._client
        return bool(client and client.is_connected())

    # --- Lifecycle ----------------------------------------------------------

    def start(self, mqtt_cfg, device_cfg):
        with self._lock:
            self.stop()
            self.cfg, self.device_cfg = mqtt_cfg, device_cfg
            self.base = device_cfg["id"]
            self.last_error = None
            if not mqtt_cfg["enabled"]:
                return
            if not mqtt_cfg["host"]:
                self.last_error = "No broker address configured."
                return
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"visiophone-{self.base}")
            if mqtt_cfg["username"]:
                client.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])
            # If the Pi disappears, the broker marks the entities unavailable
            client.will_set(self.topic("status"), "offline", retain=True)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.reconnect_delay_set(1, 60)
            client.connect_async(mqtt_cfg["host"], int(mqtt_cfg["port"]), keepalive=30)
            client.loop_start()
            self._client = client

    def stop(self):
        with self._lock:
            client, self._client = self._client, None
            if client is None:
                return
            if client.is_connected():
                client.publish(self.topic("status"), "offline", retain=True).wait_for_publish(2)
            client.disconnect()
            client.loop_stop()

    # --- Callbacks ----------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.last_error = f"Connection refused: {reason_code}"
            log.error("MQTT %s", self.last_error)
            return
        self.last_error = None
        log.info("Connected to MQTT broker %s:%s", self.cfg["host"], self.cfg["port"])
        self.publish_discovery()
        client.subscribe(self.topic("command"))
        client.subscribe(self.topic("led/set"))
        client.publish(self.topic("status"), "online", retain=True)
        self.publish_led()
        self.publish_live_view()
        self.service.wake_status()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.last_error = f"Disconnected: {reason_code}"
            log.warning("MQTT %s", self.last_error)

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode(errors="replace")
        if msg.topic == self.topic("command"):
            if payload in COMMANDS:
                log.info("Command from Home Assistant: %s", payload)
                self.service.run_command(payload)
            else:
                log.warning("Unknown command ignored: %r", payload)
        elif msg.topic == self.topic("led/set"):
            try:
                data = json.loads(payload)
            except ValueError:
                data = {"state": payload}
            on = None
            if "state" in data:
                on = str(data["state"]).upper() == "ON"
            self.service.io.set_led(on=on, brightness=data.get("brightness"), effect=data.get("effect"))

    # --- Publishing ---------------------------------------------------------

    def _publish(self, suffix_or_topic, payload, retain=False, absolute=False):
        client = self._client
        if client is None or not client.is_connected():
            return
        topic = suffix_or_topic if absolute else self.topic(suffix_or_topic)
        if not isinstance(payload, str):
            payload = json.dumps(payload)
        client.publish(topic, payload, retain=retain)

    def device(self):
        from . import system
        return {
            "identifiers": [self.base],
            "name": self.device_cfg["name"],
            "manufacturer": "Raspberry Pi",
            "model": hardware.pi_model(),
            "sw_version": f"Visiophone {__version__}",
            "configuration_url": f"http://{system.ip_address()}/",
        }

    def discovery(self):
        """(component, key, config) of every entity."""
        common = {"availability_topic": self.topic("status"), "device": self.device()}
        entities = [("event", "doorbell", {
            "name": "Doorbell", "device_class": "doorbell", "event_types": ["press"],
            "state_topic": self.topic("doorbell"),
        })]
        for key, name, unit, device_class, icon in [
            ("cpu_temperature", "CPU temperature", "°C", "temperature", None),
            ("cpu_load", "CPU load", "%", None, "mdi:cpu-64-bit"),
            ("memory_used", "Memory used", "%", None, "mdi:memory"),
            ("disk_used", "Disk used", "%", None, "mdi:sd"),
        ]:
            config = {
                "name": name, "state_topic": self.topic("system"),
                "value_template": f"{{{{ value_json.{key} }}}}", "unit_of_measurement": unit,
                "state_class": "measurement", "entity_category": "diagnostic",
            }
            if device_class:
                config["device_class"] = device_class
            if icon:
                config["icon"] = icon
            entities.append(("sensor", key, config))
        entities.append(("sensor", "boot_time", {
            "name": "Boot time", "state_topic": self.topic("system"),
            "value_template": "{{ value_json.boot_time }}", "device_class": "timestamp",
            "entity_category": "diagnostic",
        }))
        for key, name, device_class in [
            ("under_voltage", "Under-voltage", "problem"),
            ("under_voltage_since_boot", "Under-voltage since boot", "problem"),
            ("stream", "Video stream", "running"),
        ]:
            entities.append(("binary_sensor", key, {
                "name": name, "state_topic": self.topic("system"),
                "value_template": f"{{{{ value_json.{key} }}}}", "payload_on": "ON", "payload_off": "OFF",
                "device_class": device_class, "entity_category": "diagnostic",
            }))
        entities.append(("binary_sensor", "live_view", {
            "name": "Live view", "state_topic": self.topic("live_view"), "icon": "mdi:eye",
            "payload_on": "ON", "payload_off": "OFF",
        }))
        entities.append(("button", "answer", {
            "name": "Answer", "command_topic": self.topic("command"), "payload_press": "answer",
            "icon": "mdi:phone-in-talk",
        }))
        for key, name, device_class, icon in [
            ("restart_stream", "Restart video stream", None, "mdi:video-wireless"),
            ("reboot", "Reboot", "restart", None),
            ("shutdown", "Shut down", None, "mdi:power"),
        ]:
            config = {"name": name, "command_topic": self.topic("command"), "payload_press": key,
                      "entity_category": "config"}
            if device_class:
                config["device_class"] = device_class
            if icon:
                config["icon"] = icon
            entities.append(("button", key, config))
        if self.service.io.led_state()["enabled"]:
            entities.append(("light", "led", {
                "name": "Button LED", "schema": "json", "icon": "mdi:circle-outline",
                "command_topic": self.topic("led/set"), "state_topic": self.topic("led/state"),
                "brightness": True, "supported_color_modes": ["brightness"],
                "effect": True, "effect_list": list(EFFECTS),
            }))
        for component, key, config in entities:
            config.update(common, unique_id=f"{self.base}_{key}",
                          default_entity_id=f"{component}.{self.base}_{key}")
            yield component, key, config

    def publish_discovery(self):
        prefix = self.cfg["discovery_prefix"]
        led_announced = False
        for component, key, config in self.discovery():
            self._publish(f"{prefix}/{component}/{self.base}/{key}/config", config, retain=True, absolute=True)
            led_announced |= key == "led"
        if not led_announced:
            # Removes the LED entity if it was announced before
            self._publish(f"{prefix}/light/{self.base}/led/config", "", retain=True, absolute=True)

    def publish_ring(self):
        self._publish("doorbell", {"event_type": "press"})

    def publish_system(self, metrics):
        on_off = lambda value: "ON" if value else "OFF"
        payload = {
            "cpu_temperature": metrics["cpu_temperature"],
            "cpu_load": metrics["cpu_load"],
            "memory_used": metrics["memory_used"],
            "disk_used": metrics["disk_used"],
            "boot_time": metrics["boot_time"],
            "under_voltage": on_off(metrics["under_voltage"]),
            "under_voltage_since_boot": on_off(metrics["under_voltage_since_boot"]),
            "stream": on_off(metrics["go2rtc"]),
        }
        self._publish("system", payload, retain=True)

    def publish_live_view(self):
        self._publish("live_view", "ON" if self.service.live else "OFF", retain=True)

    def publish_led(self):
        state = self.service.io.led_state()
        if state["enabled"]:
            self._publish("led/state", {"state": "ON" if state["on"] else "OFF",
                                        "brightness": state["brightness"], "color_mode": "brightness",
                                        "effect": state["effect"]}, retain=True)
