# Visiophone user manual

Visiophone turns a Raspberry Pi into a video doorbell for Home Assistant:
a camera, a push button, a microphone and a speaker, all set up from a web
page. A press on the button fires an event in Home Assistant; the video is
available to Home Assistant, Frigate, VLC and browsers, with two-way audio.

## Overview

| Part | Role |
|------|------|
| **go2rtc** | Video server: publishes the camera as RTSP and WebRTC streams, and plays the browser's sound on the speaker. |
| **Visiophone service** | Web interface, doorbell button, LED ring, link to Home Assistant over MQTT. It writes go2rtc's configuration for you. |
| **Home Assistant** | Notifications, chimes, automations. Talks to the doorbell through an MQTT broker. |

Two streams are published (the name `doorbell` can be changed):

| Stream | Content | Use |
|--------|---------|-----|
| `doorbell` | Camera video (H.264) and microphone | Home Assistant, Frigate, VLC, recording |
| `doorbell_web` | Same video, Opus audio, speaker output | Browsers, two-way audio |

## What you need

- A Raspberry Pi 3, 4, 5 or Zero 2 W with Raspberry Pi OS (Bookworm or Trixie), Lite is enough.
- A camera:
    - **CSI camera** (ribbon cable): best choice on a Pi 1-4, encoded by the Pi's hardware encoder with almost no CPU load.
    - **USB camera**: prefer one with a native H.264 output; MJPEG cameras work but the Pi has to re-encode the video.
- Optional: a USB sound card with a microphone and a small amplified speaker, or a speaker on the Pi's jack output.
- A momentary push button (normally open). Vandal-proof stainless buttons with an LED ring are a good fit.
- A stable power supply. Under-voltage makes cameras and USB devices drop out; Power over Ethernet adapters work well for an outdoor post.

> **Pi 5:** it has no hardware H.264 encoder. CSI cameras are encoded in software (about one CPU core at 720p). Support is experimental.

## Wiring

### Doorbell button

Connect the button between a GPIO and a ground pin. The default is **GPIO17 (pin 11)** and **GND (pin 9)**. No resistor is needed: the Pi's internal pull-up keeps the input high and a press pulls it low.

```
 Pi pin 11 (GPIO17) ──── button ──── Pi pin 9 (GND)
```

Long cables outdoors pick up interference: keep the default debounce time (50 ms) and, if you get ghost rings, add a 100 nF capacitor across the button.

### LED ring

Default: **GPIO27 (pin 13)**. A GPIO gives 3.3 V and should not supply more than about 10 mA.

1. Measure the current the LED draws at 3.3 V (pin 1) with a multimeter.
2. **Below 10 mA and bright enough:** connect LED+ to the GPIO and LED− to GND. Brightness control (PWM) works.
3. **Otherwise:** drive it through an NPN transistor (BC547, 2N2222) fed from 5 V:

```
 GPIO27 ── 1 kΩ ── base
 5 V ── LED+ … LED− ── collector
 GND ── emitter
```

Check that your LED ring accepts 5 V (many are rated 3-6 V or 12 V; a 12 V ring needs a 12 V supply on the collector side instead).

### Camera

- **CSI:** power the Pi off, lift the connector latch, insert the ribbon (contacts towards the board on Pi 3/4), close the latch.
- **USB:** plug it in. Use the ports directly on the Pi rather than a hub.

### Sound

- **USB sound card** with microphone and speaker: plug it in. Using the same card for both directions keeps them on the same clock, which echo cancellation will need.
- **Jack output** (Pi 1-4): connect an amplified speaker. The Pi has no microphone input.

## Installation

On the Pi, in a terminal (or over SSH):

```sh
sudo apt install -y git
git clone https://github.com/<your-account>/visiophone.git
cd visiophone
sudo ./install.sh
```

The installer:

1. installs the packages it needs (Python libraries, rpicam-apps, ffmpeg, v4l-utils, alsa-utils);
2. downloads go2rtc;
3. creates a `visiophone` system user with access to the camera, sound and GPIO;
4. detects the camera and sound devices and writes a first configuration;
5. asks for the password of the web interface;
6. starts the `go2rtc` and `visiophone` services, which then start at every boot.

To update later: `git pull` then `sudo ./install.sh` again. Your configuration is kept.

## First steps

Open `http://<pi-address>/` in a browser and log in. Then:

1. **Camera**: check the preview. If your CSI camera is not listed, declare it (see below).
2. **Audio**: choose the microphone and speaker, play a test tone.
3. **Button & LED**: press the button and watch the test indicator.
4. **Home Assistant**: enter the MQTT broker address and account.
5. Mount the doorbell, then fix the image orientation on the Camera page.

## Status page

Shows a camera snapshot (refreshed every few seconds), the state of each part and the Pi's health:

- **Video server**: go2rtc running or not. *Stream clients* is the number of viewers right now. The camera only runs while somebody watches.
- **Home Assistant (MQTT)**: connected, disconnected (with the reason) or disabled.
- **Button**: ready, or the error that prevents using the GPIO.
- **Power supply**: *dips since boot* means the voltage dropped at least once; *under-voltage* means it is too low right now.

The **Live view with two-way audio** button opens go2rtc's WebRTC player with the microphone enabled.

## Camera

### Detected cameras

The page lists the CSI and USB cameras found at start. Click **Scan again** after plugging a USB camera.

### Declaring a CSI camera

Official Raspberry Pi camera modules carry an ID chip and are detected automatically. Most third-party modules (wide-angle, night vision…) are not. In **CSI camera declaration**, pick the sensor model printed on the module or in its description (IMX219, OV5647, IMX708…), keep **Reboot now** checked and apply. The Pi reboots with the right driver.

This edits a block in `/boot/firmware/config.txt` marked `visiophone camera`; a copy of the previous file is kept as `config.txt.visiophone-backup`.

### Settings

| Setting | Notes |
|---------|-------|
| **NoIR** | For sensors without an infrared filter: if the image looks pink or magenta in daylight, check it. Night-vision modules with IR LEDs usually need it. |
| **Full field of view** | Uses a sensor mode that reads the whole sensor. Without it, some resolutions crop the centre of the image, which loses most of a wide-angle lens. |
| **Resolution** | 4:3 resolutions (1280×960) keep the whole image of 4:3 sensors such as the IMX219 and show visitors from head to toe. 1920×1080 is the maximum. |
| **Frame rate** | 10-15 fps is plenty for a doorbell and saves bandwidth. |
| **Bitrate** | 1500-3000 kbit/s for 720p/960p. Raise it if the image gets blocky when people move. |
| **Orientation** | 180° for a camera mounted upside down; mirror/flip for other mounting positions. |
| **USB format** | H.264 when the camera offers it (no load on the Pi). MJPEG/YUYV are re-encoded by the Pi. |
| **Re-encode for browsers** | USB cameras often send H.264 Main/High profile, which some browsers (Firefox on Linux) cannot play: check this if the live view stays black. Adds about 0.5 s of delay. |

Saving restarts the video server: viewers are disconnected for a few seconds.

## Audio

Choose the **microphone** (sent with the video) and the **speaker** (plays your voice). Volumes are saved and restored at boot. **Play a test tone** checks the speaker.

### Two-way audio in a browser

Browsers only give access to your microphone on secure pages (`https://` or `localhost`). On a plain `http://` local address you can:

- **Use Home Assistant** over HTTPS (Nabu Casa, or your own certificate) with a WebRTC camera card: the most comfortable option.
- **Firefox:** in `about:config`, set `media.devices.insecure.enabled` and `media.getusermedia.insecure.enabled` to `true`.
- **Chrome / Edge:** open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, add `http://<pi-address>:1984`, enable and relaunch.

### Echo

If the visitor hears themselves, the microphone picks up the speaker. Move them apart, lower the speaker volume, or point the speaker away from the microphone. Software echo cancellation is planned.

## Button & LED

| Setting | Notes |
|---------|-------|
| **GPIO** | BCM numbering; the header pin is shown next to it. Avoid pins marked I2C, UART or SPI if you use those buses. |
| **Wiring** | *Between the GPIO and GND* (recommended) or *between the GPIO and 3.3 V*. |
| **Debounce** | Ignores contact bounce shorter than this. |
| **Minimum time between rings** | An impatient visitor pressing ten times only rings once in this delay. |
| **LED: on at start, brightness** | Steady state of the ring. Home Assistant can change it at any time. |
| **Blink after a ring** | Shows the visitor that the press was taken into account. |

The grey dot turns green while the button is held: use it to check the wiring. **Simulate a ring** sends a ring to Home Assistant without touching the button.

## Home Assistant

### MQTT connection

1. In Home Assistant, install the **Mosquitto broker** add-on (or use your own broker) and the **MQTT** integration.
2. Create a Home Assistant user for the doorbell (Settings → People → Users), for example `visiophone`.
3. On the **Home Assistant** page of the doorbell, enter the broker address (your Home Assistant address), port 1883, this user and its password, check **Connect** and save.

A **Doorbell** device appears in Settings → Devices → MQTT.

### Entities

| Entity | Use |
|--------|-----|
| `event.visiophone_doorbell` | Fires `press` on each ring. |
| `light.visiophone_led` | LED ring, with brightness (when the LED is enabled). |
| `button.visiophone_restart_stream`, `_reboot`, `_shutdown` | Maintenance. |
| `sensor.visiophone_cpu_temperature`, `_cpu_load`, `_memory_used`, `_disk_used`, `_boot_time` | Diagnostics. |
| `binary_sensor.visiophone_stream`, `_under_voltage`, `_under_voltage_since_boot` | Diagnostics. |

`visiophone` is the device ID (Home Assistant page → Advanced).

### Adding the camera

Use the **go2rtc** support built into Home Assistant, or the **Generic Camera** integration with:

- Stream source: `rtsp://<pi-address>:8554/doorbell`
- Still image: `http://<pi-address>:1984/api/frame.jpeg?src=doorbell`

For two-way audio in dashboards, the [WebRTC Camera](https://github.com/AlexxIT/WebRTC) custom card can use `doorbell_web` with the microphone.

### Example automation

Ring a speaker and send a notification with a snapshot:

```yaml
alias: Doorbell
triggers:
  - trigger: state
    entity_id: event.visiophone_doorbell
    not_from: unavailable
actions:
  - action: media_player.play_media
    target:
      entity_id: media_player.kitchen
    data:
      media_content_id: media-source://media_source/local/ring.mp3
      media_content_type: music
  - action: notify.mobile_app_my_phone
    data:
      title: Doorbell
      message: Someone is at the door
      data:
        image: /api/camera_proxy/camera.doorbell
mode: single
```

`not_from: unavailable` avoids a ring when the doorbell reconnects.

## Frigate

Frigate can record and detect people on the `doorbell` stream:

```yaml
go2rtc:
  streams:
    doorbell: rtsp://<pi-address>:8554/doorbell
cameras:
  doorbell:
    ffmpeg:
      inputs:
        - path: rtsp://127.0.0.1:8554/doorbell
          roles: [detect, record]
```

Pull a single stream from the Pi (through Frigate's own go2rtc as above) rather than several: the Pi's network and encoder are limited.

## System

- **Restart the video stream**, **Reboot**, **Shut down**.
- **Show the logs**: the last lines of the Visiophone and go2rtc logs.
- **Web interface password**: at least 8 characters. Forgotten? On the Pi: `sudo visiophone-passwd`.
- **Stream name** and **stream protection**: without protection anyone on your network can watch the camera and use the go2rtc page on port 1984. With a user name and password, RTSP clients need `rtsp://user:password@<pi-address>:8554/doorbell` and the go2rtc page asks for them.

## Troubleshooting

| Problem | What to check |
|---------|---------------|
| CSI camera not listed | Ribbon orientation and latch; declare the sensor on the Camera page; `rpicam-hello --list-cameras` on the Pi. |
| Black or broken snapshot | Status page: is go2rtc running? Open the go2rtc page (port 1984) to see the stream error. Show the logs. |
| Pink or magenta image | Check **NoIR** on the Camera page. |
| Image upside down | Camera page → Orientation. |
| Live view black in Firefox | USB camera in H.264 Main/High: check **Re-encode for browsers**. |
| No microphone in the browser | The page is not secure: see *Two-way audio in a browser*. |
| Test tone: “speaker in use” | A browser is talking through the speaker; close the live view. |
| Button does nothing | Status page shows the GPIO error; check the GPIO number and the wiring with the test dot. |
| Home Assistant not connected | The status shows the reason: wrong password (*Not authorized*), wrong address (*connection refused* / timeout). |
| Ghost rings | Raise the debounce time; add a 100 nF capacitor across the button; use shielded or twisted cable. |
| Delay of 1-2 s | Normal for RTSP/HLS players; WebRTC (live view) is the fastest. |

Logs from a terminal:

```sh
journalctl -u visiophone -u go2rtc -f
```

## Command line

| Command | Use |
|---------|-----|
| `sudo visiophone-passwd` | Set the web interface password. |
| `sudo systemctl restart visiophone` | Restart the service. |
| `sudo -u visiophone PYTHONPATH=/opt/visiophone python3 -m visiophone scan` | Show the detected hardware. |
| `sudo -u visiophone PYTHONPATH=/opt/visiophone python3 -m visiophone go2rtc` | Show the go2rtc configuration that would be generated. |

## Files

| Path | Content |
|------|---------|
| `/etc/visiophone/config.yaml` | Configuration (written by the web interface). |
| `/etc/visiophone/go2rtc.yaml` | go2rtc configuration, generated: do not edit. |
| `/opt/visiophone/` | Program. |
| `/usr/local/sbin/visiophone-admin` | Privileged actions (reboot, camera declaration…), the only command the service may run as root. |
| `/var/lib/visiophone/` | Cache (camera modes). |

## Uninstall

```sh
sudo ./uninstall.sh
```

The configuration in `/etc/visiophone` is kept unless you add `--purge`.
