# Visiophone

A Raspberry Pi video doorbell for Home Assistant, configured from a web page.

- **Camera**: CSI or USB, detected automatically, H.264 encoded by the Pi's hardware encoder.
- **Streams** through [go2rtc](https://github.com/AlexxIT/go2rtc): RTSP for Home Assistant and Frigate, WebRTC with two-way audio for browsers.
- **Doorbell button** on a GPIO, with an optional LED ring (dimmable, blinks after a ring).
- **Home Assistant** over MQTT discovery: doorbell event, LED light, diagnostics, reboot buttons. No YAML needed.
- **Video message** blueprint for Home Assistant: greeting and recording when nobody answers.
- **Web interface**: status, camera, audio, button, Home Assistant and system settings, built-in manual. Password protected.

## Requirements

- Raspberry Pi 3, 4, 5 or Zero 2 W (Pi 5: software encoding, experimental)
- Raspberry Pi OS Bookworm or Trixie (Lite is enough)
- A CSI or USB camera, a push button; optionally a USB sound card and a speaker

## Install

```sh
git clone https://github.com/Charly314159/Raspberry-Pi-Doorbell.git
cd Raspberry-Pi-Doorbell
sudo ./install.sh
```

Then open `http://<pi-address>/`. Update with `git pull && sudo ./install.sh`.

## Home Assistant blueprint

[![Import the video message blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FCharly314159%2FRaspberry-Pi-Doorbell%2Fblob%2Fmain%2Fhomeassistant%2Fblueprints%2Fvideo_message.yaml)

Video message when nobody answers: greeting, press to record, LED feedback. See the manual.

## Documentation

The [user manual](docs/MANUAL.md) covers wiring, every setting, Home Assistant and Frigate integration and troubleshooting. It is also available in the web interface (**Manual**).

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install flask waitress pyyaml markdown paho-mqtt gpiozero pytest
.venv/bin/python -m pytest
```

Layout:

| Path | Content |
|------|---------|
| `visiophone/config.py` | Configuration file and defaults |
| `visiophone/hardware.py` | Detection of cameras, sound cards, Pi model |
| `visiophone/go2rtc.py` | go2rtc configuration generation and API |
| `visiophone/gpio.py` | Button and LED |
| `visiophone/mqtt.py` | Home Assistant MQTT discovery |
| `visiophone/web.py`, `templates/`, `static/` | Web interface |
| `system/` | systemd units, sudoers rule, privileged helper |

## License

MIT
