"""
Bridges the Arduino accessibility buttons (USB serial) to the backend.
The Arduino prints lines like  BTN:REPEAT  /  BTN:LANG  /  BTN:NEXT

Run:  python serial_bridge.py      (env: SERIAL_PORT e.g. COM3 or /dev/ttyACM0, BACKEND)
"""
import os
import time

import requests
import serial
from serial.tools import list_ports

BACKEND = os.getenv("BACKEND", "http://127.0.0.1:8000")


def find_port():
    if os.getenv("SERIAL_PORT"):
        return os.getenv("SERIAL_PORT")
    for p in list_ports.comports():
        desc = f"{p.description} {p.manufacturer or ''}".lower()
        if any(k in desc for k in ("arduino", "ch340", "usb serial", "usb-serial", "cp210")):
            return p.device
    return None


def main():
    while True:
        port = find_port()
        if not port:
            print("Arduino not found. Plug it in or set SERIAL_PORT. Retrying in 3 s...")
            time.sleep(3)
            continue
        try:
            with serial.Serial(port, 9600, timeout=1) as ser:
                print(f"Listening on {port}")
                while True:
                    line = ser.readline().decode(errors="ignore").strip()
                    if line.startswith("BTN:"):
                        name = line[4:]
                        requests.post(f"{BACKEND}/api/sensor/event",
                                      json={"type": "button", "source": "kiosk-buttons", "value": name}, timeout=3)
                        print("button", name)
        except (serial.SerialException, requests.RequestException) as e:
            print("connection lost:", e)
            time.sleep(2)


if __name__ == "__main__":
    main()
