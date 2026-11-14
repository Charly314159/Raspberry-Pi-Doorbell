from visiophone import hardware

IDLE = """Available cameras
-----------------
0 : imx219 [3280x2464 10-bit RGGB] (/base/soc/i2c0mux/i2c@1/imx219@10)
    Modes: 'SRGGB10_CSI2P' : 640x480 [200.16 fps - (1000, 752)/1280x960 crop]
                             1640x1232 [81.07 fps - (0, 0)/3280x2464 crop]
                             1920x1080 [47.57 fps - (680, 692)/1920x1080 crop]
                             3280x2464 [21.19 fps - (0, 0)/3280x2464 crop]
           'SRGGB8' : 640x480 [200.16 fps - (1000, 752)/1280x960 crop]
                      1640x1232 [81.07 fps - (0, 0)/3280x2464 crop]
"""

BUSY = """Available cameras
-----------------
0 : imx219 [3280x2464 10-bit] (/base/soc/i2c0mux/i2c@1/imx219@10)
    Modes: 'SBGGR10_CSI2P' : 640x480 [30.00 fps - (65535, 65535)/65535x65535 crop]
                             1640x1232 [30.00 fps - (65535, 65535)/65535x65535 crop]
"""

V4L2 = """ioctl: VIDIOC_ENUM_FMT
	Type: Video Capture

	[0]: 'MJPG' (Motion-JPEG, compressed)
		Size: Discrete 1920x1080
			Interval: Discrete 0.033s (30.000 fps)
		Size: Discrete 1280x720
			Interval: Discrete 0.033s (30.000 fps)
			Interval: Discrete 0.067s (15.000 fps)
	[1]: 'YUYV' (YUYV 4:2:2)
		Size: Discrete 640x480
			Interval: Discrete 0.033s (30.000 fps)
	[2]: 'H264' (H.264, compressed)
		Size: Discrete 1280x720
			Interval: Discrete 0.033s (30.000 fps)
"""


def test_parse_idle_camera():
    (cam,) = hardware.parse_camera_list(IDLE)
    assert cam["sensor"] == "imx219" and cam["index"] == 0 and not cam["busy"]
    assert [m["mode"] for m in cam["modes"]] == ["640:480:10:P", "1640:1232:10:P", "1920:1080:10:P", "3280:2464:10:P"]
    assert [m["full_fov"] for m in cam["modes"]] == [False, True, False, True]


def test_parse_busy_camera():
    (cam,) = hardware.parse_camera_list(BUSY)
    assert cam["busy"] and cam["modes"] == []


def test_parse_v4l2_formats():
    formats = hardware.parse_v4l2_formats(V4L2)
    assert formats == {
        "mjpeg": {"1920x1080": [30.0], "1280x720": [30.0, 15.0]},
        "yuyv": {"640x480": [30.0]},
        "h264": {"1280x720": [30.0]},
    }


def test_led_effects():
    from gpiozero import Device
    from gpiozero.pins.mock import MockFactory, MockPWMPin
    Device.pin_factory = MockFactory(pin_class=MockPWMPin)
    from visiophone.gpio import DoorbellIO
    io = DoorbellIO(lambda: None)
    io.configure({"enabled": False, "gpio": 17, "pull_up": True, "debounce_ms": 50, "min_interval_s": 0},
                 {"enabled": True, "gpio": 27, "on_at_start": True, "brightness": 128,
                  "blink_on_ring": True, "blink_duration_s": 1})
    assert io.led_state() == {"enabled": True, "on": True, "brightness": 128, "effect": "none"}
    io.set_led(effect="breathe")
    assert io.led_state()["effect"] == "breathe"
    io.set_led(effect="none")
    assert abs(io._led.value - 128 / 255) < 0.01
    io.set_led(on=False)
    assert io._led.value == 0
    io.close()


def test_led_effects_peak_at_brightness():
    import time
    from gpiozero import Device
    from gpiozero.pins.mock import MockFactory, MockPWMPin
    Device.pin_factory = MockFactory(pin_class=MockPWMPin)
    from visiophone.gpio import DoorbellIO
    io = DoorbellIO(lambda: None)
    io.configure({"enabled": False, "gpio": 17, "pull_up": True, "debounce_ms": 50, "min_interval_s": 0},
                 {"enabled": True, "gpio": 27, "on_at_start": True, "brightness": 51,
                  "blink_on_ring": True, "blink_duration_s": 1})
    peak = 51 / 255
    seen = []
    io.set_led(effect="fast_blink")
    for _ in range(40):
        seen.append(io._led.value)
        time.sleep(0.01)
    assert max(seen) <= peak + 1e-6 and max(seen) > 0
    seen.clear()
    io.set_led(effect="breathe")
    for _ in range(150):
        seen.append(io._led.value)
        time.sleep(0.01)
    assert max(seen) <= peak + 1e-6 and min(seen) < peak / 2
    io.set_led(effect="none")
    assert abs(io._led.value - peak) < 1e-6
    io.close()


def test_sound_levels():
    import struct
    from visiophone import sound
    assert sound.level_from_s16le(b"\x00\x00" * 320) == 0.0
    loud = struct.pack("<320h", *([12000, -12000] * 160))
    assert sound.level_from_s16le(loud) > 0.9
    quiet = struct.pack("<320h", *([400, -400] * 160))
    assert 0 < sound.level_from_s16le(quiet) < 0.3
    assert sound.ALAW_TABLE[0xD5] in (8, -8)          # A-law silence
    assert sound.level_from_alaw(bytes([0xD5]) * 320) == 0.0


def test_led_follows_sound():
    import time
    from gpiozero import Device
    from gpiozero.pins.mock import MockFactory, MockPWMPin
    Device.pin_factory = MockFactory(pin_class=MockPWMPin)
    from visiophone import gpio
    gpio.SOUND_SILENCE_S = 0.3
    io = gpio.DoorbellIO(lambda: None)
    io.configure({"enabled": False, "gpio": 17, "pull_up": True, "debounce_ms": 50, "min_interval_s": 0},
                 {"enabled": True, "gpio": 27, "on_at_start": False, "brightness": 200,
                  "blink_on_ring": True, "blink_duration_s": 1, "follow_sound": True})
    assert io._led.value == 0
    io.feed_sound(0.8)
    time.sleep(0.1)
    assert 0.5 < io._led.value <= 200 / 255 + 1e-6
    io.feed_sound(0.0)
    time.sleep(1.2)                       # silence: back to the steady state (off)
    assert io._led.value == 0 and not io._following_sound
    io.close()
