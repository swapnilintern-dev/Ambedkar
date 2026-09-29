"""
Visitor detection with the laptop webcam (replaces a PIR sensor).
Compares frames in memory only: no faces are detected and no images are saved.

Run:  python motion.py            (env: BACKEND, CAM_INDEX, MOTION_THRESHOLD, COOLDOWN)
"""
import os
import time

import cv2
import requests

BACKEND = os.getenv("BACKEND", "http://127.0.0.1:8000")
CAM_INDEX = int(os.getenv("CAM_INDEX", "0"))
THRESHOLD = float(os.getenv("MOTION_THRESHOLD", "0.04"))   # fraction of pixels that must change
COOLDOWN = float(os.getenv("COOLDOWN", "20"))              # seconds between visitor events


def main():
    cap = cv2.VideoCapture(CAM_INDEX)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open webcam {CAM_INDEX}. Close other apps using it or set CAM_INDEX.")
    avg, last = None, 0.0
    print(f"Watching webcam {CAM_INDEX}; posting visitor events to {BACKEND}")
    while True:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.5)
            continue
        gray = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(frame, (320, 240)), cv2.COLOR_BGR2GRAY), (21, 21), 0)
        if avg is None:
            avg = gray.astype("float")
            continue
        cv2.accumulateWeighted(gray, avg, 0.1)
        diff = cv2.absdiff(gray, cv2.convertScaleAbs(avg))
        _, th = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        ratio = cv2.countNonZero(th) / th.size
        if ratio > THRESHOLD and time.time() - last > COOLDOWN:
            last = time.time()
            try:
                requests.post(f"{BACKEND}/api/sensor/event",
                              json={"type": "visitor", "source": "kiosk-webcam", "value": f"{ratio:.2f}"}, timeout=3)
                print(f"visitor detected (motion {ratio:.0%})")
            except requests.RequestException as e:
                print("backend unreachable:", e)
        time.sleep(0.1)


if __name__ == "__main__":
    main()
