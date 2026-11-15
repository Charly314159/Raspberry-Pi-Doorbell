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
git clone https://github.com/Charly314159/Raspberry-Pi-Doorbell.git
cd Raspberry-Pi-Doorbell
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
| **Resolution** | 4:3 resolutions (1280×960, 1440×1080) keep the whole image of 4:3 sensors such as the IMX219: a 16:9 resolution (1280×720, 1920×1080) keeps the full width but cuts about a quarter of the height. The hardware encoder is limited to 1920×1080, so 1440×1080 is the sharpest full-field choice. |
| **Frame rate** | 10-15 fps is plenty for a doorbell and saves bandwidth. |
| **Bitrate** | 1500-3000 kbit/s for 720p/960p. Raise it if the image gets blocky when people move. |
| **Orientation** | 180° for a camera mounted upside down; mirror/flip for other mounting positions. |
| **USB format** | H.264 when the camera offers it (no load on the Pi). MJPEG/YUYV are re-encoded by the Pi. |
| **Re-encode for browsers** | USB cameras often send H.264 Main/High profile, which some browsers (Firefox on Linux) cannot play: check this if the live view stays black. Adds about 0.5 s of delay. |

Saving restarts the video server: viewers are disconnected for a few seconds.

### Image (CSI cameras)

Adjust the picture while watching the preview; each **Apply** restarts the stream for a few seconds.

| Setting | Notes |
|---------|-------|
| **Brightness** | Shifts all tones; prefer **Exposure compensation** for a too dark or too bright image. |
| **Contrast**, **Saturation**, **Sharpness** | 1 is neutral. Saturation 0 gives black and white. |
| **Exposure compensation (EV)** | +1 doubles the exposure, −1 halves it. Useful against backlight (a bright street behind the visitor): raise it. |
| **Exposure metering** | *Centre-weighted* suits a doorbell; *Spot* exposes for the very centre (the visitor's face) and ignores a bright background. |
| **Noise reduction** | *High quality* gives a cleaner image at night. |
| **White balance** | *Automatic* by default. If colours are off, try a preset (*Indoor*, *Daylight*…) or *Manual gains*: raise red against a greenish/blue image, raise blue against a yellow/orange one. |

**Back to defaults** removes every adjustment.

Exposure and white balance are automatic: the camera adapts its exposure time and gain to the light all day long. *Exposure compensation* only shifts that automatic exposure.

### Colour calibration

Sensors without an infrared filter never give natural colours with their stock tuning. A **colour matrix** (Camera → Image → Colour calibration) corrects them: 9 numbers, row by row, applied on top of the sensor's colour correction for every light temperature (the doorbell writes its own tuning file in `/var/lib/visiophone/`).

To measure it, film the same scene with the doorbell and a reference camera with good colours, **with the infrared LEDs covered**:

1. take a frame of each camera under the same lighting;
2. average the colour of matching areas on both (white ceiling and walls, a grey sofa, a black bag, a few saturated objects);
3. convert both to linear RGB, scale each doorbell patch to the reference luminance (only the colour is compared), and fit the 3×3 matrix by least squares;
4. apply it, take a new frame and repeat once with the remaining error: the camera's tone curve is not exactly sRGB, so two passes converge better than one.

Check the result in daylight too: a matrix measured under artificial light may need a second calibration for daylight.
 Sensors without an infrared filter (NoIR) never give perfectly natural colours in daylight: the NoIR tuning and the white balance only limit the effect.

## Audio

Choose the **microphone** (sent with the video) and the **speaker** (plays your voice). Without a microphone yet, choose **Test tone**: the doorbell sends a beep every second instead, to check that its sound reaches Home Assistant, Frigate or a browser (unmute the viewer). Volumes are saved and restored at boot. **Play a test tone** checks the speaker.

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
| **Follow the sound** | While the doorbell speaks (voice messages, your voice from the browser), the LED follows the volume, then returns to its previous state. See *LED and sound* below. |

### LED and sound

The LED can follow the volume of what the doorbell plays, with the **Follow the sound** option or the `sound` effect (the LED then only lights with the sound). Effects never exceed the brightness you set.

- **Your voice from the browser** goes through Visiophone's speaker relay, which measures its level: nothing to set up.
- **Voice messages played by MPD** (text-to-speech from Home Assistant) need a second MPD output that copies the sound for Visiophone. Add to the MPD configuration, then restart MPD:

```
audio_output {
	type		"fifo"
	name		"Visiophone LED"
	path		"/run/visiophone/sound.fifo"
	format		"8000:16:1"
}
```

Any other player can feed the same FIFO with raw signed 16-bit mono 8 kHz audio.

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
| `light.visiophone_led` | LED ring, with brightness and effects (`blink`, `fast_blink`, `breathe`, `sound`), when the LED is enabled. |
| `binary_sensor.visiophone_live_view` | On while someone watches the live view in a browser (go2rtc page, Advanced Camera Card). RTSP clients (recordings, Frigate) do not count. |
| `button.visiophone_answer` | Tells the doorbell somebody answers: stops the LED blinking and cancels the video message. |
| `button.visiophone_restart_stream`, `_reboot`, `_shutdown` | Maintenance. |
| `sensor.visiophone_cpu_temperature`, `_cpu_load`, `_memory_used`, `_disk_used`, `_boot_time` | Diagnostics. |
| `binary_sensor.visiophone_stream`, `_under_voltage`, `_under_voltage_since_boot` | Diagnostics. |

`visiophone` is the device ID (Home Assistant page → Advanced).

### Adding the camera

Use the **go2rtc** support built into Home Assistant, or the **Generic Camera** integration with:

- Stream source: `rtsp://<pi-address>:8554/doorbell`
- Still image: `http://<pi-address>:1984/api/frame.jpeg?src=doorbell`

### Live view and two-way audio in a dashboard

The [Advanced Camera Card](https://github.com/dermotduffy/advanced-camera-card) can connect straight to the doorbell's go2rtc, with the microphone:

```yaml
type: custom:advanced-camera-card
cameras:
  - camera_entity: camera.visiophone
    live_provider: go2rtc
    go2rtc:
      url: http://<pi-address>:1984
      stream: doorbell_web
menu:
  buttons:
    microphone:
      enabled: true
```

How the card reaches the doorbell depends on how you open Home Assistant:

- **Home Assistant over HTTPS** (remote access, DuckDNS, Nabu Casa, the app with an HTTPS address): the browser refuses plain-HTTP connections from an HTTPS page, so the card cannot reach the doorbell directly. Use `live_provider: ha` instead (Home Assistant's own stream, through the camera entity): it works locally and remotely, but without the microphone, and opening it does not count as an answer. The *Hass Web Proxy* integration would relay the doorbell's go2rtc through Home Assistant, but **do not install it next to the Frigate integration**: they require different versions of the same library, and Frigate fails to start.
- **Home Assistant over plain HTTP** on your network: on the doorbell's **System** page, check **Allow dashboards served by another address to open the live player**, or the browser blocks the connection. Viewers connected this way are seen by the doorbell: `binary_sensor.visiophone_live_view` turns on, which the video message blueprint takes as an answer.

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

### Video message when nobody answers

The repository ships a Home Assistant blueprint, `homeassistant/blueprints/video_message.yaml`:

1. After a ring, it waits for somebody to answer (30 s by default).
2. Without an answer, a voice message invites the visitor to press the button to leave a video message. The LED ring breathes.
3. The visitor presses: recording starts, the LED blinks and a voice message tells them to speak.
4. The visitor presses again to finish (or the maximum length, 40 s, is reached): a thank-you message plays, the LED returns to its previous state.
5. Your actions run, typically a notification with a snapshot.

Home Assistant cannot stop a recording early, so the clip always lasts the maximum length; the second press ends the message for the visitor.

Somebody answers when:

- the live view is opened in a browser (`binary_sensor.visiophone_live_view`), for example with the Advanced Camera Card;
- `button.visiophone_answer` is pressed (dashboard button);
- a notification button with the action `VISIOPHONE_ANSWER` is tapped. Add it to your ring notification:

```yaml
data:
  actions:
    - action: VISIOPHONE_ANSWER
      title: Answer
```

Setup:

1. Create a toggle helper (**Settings → Helpers → Toggle**), for example *Doorbell message in progress*.
2. Add a condition to your ring automation so that the presses of a message do not ring. The second line is a safety net in case the helper stays on:

```yaml
conditions:
  - condition: or
    conditions:
      - condition: state
        entity_id: input_boolean.doorbell_message_in_progress
        state: "off"
      - condition: state
        entity_id: input_boolean.doorbell_message_in_progress
        state: "on"
        for:
          minutes: 3
```

3. Import the blueprint: [open it in your Home Assistant](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2FCharly314159%2FRaspberry-Pi-Doorbell%2Fblob%2Fmain%2Fhomeassistant%2Fblueprints%2Fvideo_message.yaml), or copy the file to `config/blueprints/automation/visiophone/`. Then **Settings → Automations → Blueprints → Visiophone – video message**.
4. Pick the doorbell event, live view sensor, Answer button, camera entity (see *Adding the camera*), the helper and, if enabled, the LED light.
5. For the voice messages, pick a text-to-speech engine and a media player that plays on the doorbell, for example the Music Player Daemon integration pointed at an MPD server on the Pi.
6. In **Actions**, add a notification. `snapshot_url` (image) and `clip_url` (video) can be used in it:

```yaml
- action: notify.mobile_app_my_phone
  data:
    message: A visitor left a video message.
    data:
      image: "{{ snapshot_url }}"
```

Clips are saved in `/media/visiophone/` and appear in **Media → My media**. Delete old ones from time to time.

The LED light has the effects `none` (steady), `blink`, `fast_blink`, `breathe` and `sound` (follows the volume of what the doorbell plays), usable in your own automations too:

```yaml
- action: light.turn_on
  target:
    entity_id: light.visiophone_led
  data:
    effect: breathe
```

> The doorbell must be able to download the voice messages from Home Assistant. If the media player logs *HTTP 403*, Home Assistant has banned the Pi's address after failed logins: remove it from `ip_bans.yaml` and restart Home Assistant.

## Frigate

Frigate can record and detect people on the doorbell stream, and its Home Assistant integration gives dashboards a live view with **two-way audio** that works over HTTPS and remotely (through Frigate's own proxy).

In Frigate's configuration (replace `doorbell` with your stream name):

```yaml
go2rtc:
  streams:
    doorbell:
      - rtsp://<pi-address>:8554/doorbell
      # Two-way audio: your voice goes back to the doorbell's speaker
      - webrtc:ws://<pi-address>:1984/api/ws?src=doorbell_talk
  # Needed by the dashboard call (WebRTC): Frigate's go2rtc must announce an
  # address phones can reach, not its container address
  webrtc:
    candidates:
      - <home-assistant-address>:8555
      - stun:8555
cameras:
  doorbell:
    ffmpeg:
      inputs:
        - path: rtsp://127.0.0.1:8554/doorbell
          input_args: preset-rtsp-restream
          roles: [detect]
    detect:
      enabled: false        # turn on for person detection
    record:
      enabled: false        # turn on to record
```

The doorbell publishes `<name>_talk`, a stream that only carries the speaker: Frigate's go2rtc connects to it when someone talks. It does not count as a live viewer, but **speaking through the doorbell counts as an answer** for the video message blueprint.

Dashboard card (Advanced Camera Card, Home Assistant opened over HTTPS):

```yaml
type: custom:advanced-camera-card
cameras:
  - camera_entity: camera.doorbell     # created by the Frigate integration
    live_provider: go2rtc
    go2rtc:
      stream: doorbell
      metadata_fetch_timeout_seconds: 10
    capabilities:
      force: [2-way-audio]
menu:
  buttons:
    microphone:
      enabled: true
      type: toggle
```

With `menu.style: overlay` the card's menu stays visible over the picture. Tap its **phone icon** to call the doorbell and talk; with `live.microphone.auto_unmute: [call]` your microphone opens at once. Tap it again to hang up. To make the card ring when the button is pressed (Answer / Reject for 30 s), add `triggers: {entities: [event.visiophone_doorbell]}` to the camera and `view: {triggers: {actions: {trigger: call, untrigger: call}}}` to the card.

If the video plays but the call does nothing, check the `webrtc` candidates above: video can fall back to MSE over the websocket, the call cannot. Remote calls (mobile data) worked with the `stun:8555` candidate in testing.

Keeping a Frigate camera on the doorbell also keeps its camera running all the time, which avoids repeated start/stop cycles of the Pi's video encoder.

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
| `/usr/local/bin/visiophone-speaker` | Speaker relay started by go2rtc for two-way audio (plays your voice, measures its level). |
| `/usr/local/sbin/visiophone-admin` | Privileged actions (reboot, camera declaration…), the only command the service may run as root. |
| `/var/lib/visiophone/` | Cache (camera modes). |

## Uninstall

```sh
sudo ./uninstall.sh
```

The configuration in `/etc/visiophone` is kept unless you add `--purge`.
