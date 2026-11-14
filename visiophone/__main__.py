"""Command line entry point.

  python3 -m visiophone            run the service (web interface, button, MQTT)
  python3 -m visiophone passwd     set the web interface password
  python3 -m visiophone scan       print the detected hardware
  python3 -m visiophone go2rtc     print the go2rtc configuration that would be generated
  python3 -m visiophone autoconfig choose devices from the detected hardware
"""
import argparse
import getpass
import json
import logging
import signal
import sys


def run():
    from waitress import serve

    from .service import Visiophone
    from .web import create_app

    service = Visiophone()
    service.start()
    app = create_app(service)
    port = service.cfg.section("web")["port"]

    def stop(*_):
        service.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    logging.getLogger(__name__).info("Web interface on port %d", port)
    serve(app, host="0.0.0.0", port=port, threads=4, ident="visiophone")


def passwd():
    from .config import Config
    from .web import hash_password

    while True:
        password = getpass.getpass("New web interface password (8 characters minimum): ")
        if len(password) < 8:
            print("Too short.")
            continue
        if password != getpass.getpass("Confirm: "):
            print("The passwords do not match.")
            continue
        break
    cfg = Config()
    cfg.update("web", {"password_hash": hash_password(password)})
    print("Password saved.")


def main():
    parser = argparse.ArgumentParser(prog="visiophone", description="Raspberry Pi video doorbell")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "passwd", "scan", "go2rtc", "autoconfig"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.command == "run":
        run()
    elif args.command == "passwd":
        passwd()
    elif args.command == "scan":
        from . import hardware
        print(json.dumps(hardware.scan(), indent=2))
    elif args.command == "autoconfig":
        from . import autoconfig
        from .config import Config
        proposal = autoconfig.apply(Config())
        camera, audio = proposal["camera"], proposal["audio"]
        if camera:
            device = camera.get("usb_device") or f"CSI camera {camera['csi_index']}"
            print(f"Camera: {camera['source'].upper()} {device}, {camera['width']}x{camera['height']}")
        else:
            print("Camera: none found")
        print(f"Microphone: {audio.get('microphone') or 'none found'}")
        print(f"Speaker: {audio.get('speaker') or 'none found'}")
    elif args.command == "go2rtc":
        from . import go2rtc, hardware
        from .config import Config
        text, warnings = go2rtc.render(Config(), hardware.scan())
        print(text)
        for warning in warnings:
            print("# WARNING:", warning, file=sys.stderr)


if __name__ == "__main__":
    main()
