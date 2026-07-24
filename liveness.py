"""Liveness detection — live Eye Aspect Ratio (EAR) and blink counting.

Uses the MediaPipe Tasks FaceLandmarker (the legacy `mp.solutions` API was
removed in mediapipe 0.10.35). The landmark model downloads on first run.

Run:  python liveness.py
Press ESC to quit. Watch the EAR value collapse when you blink.
"""

import logging_setup  # noqa: F401  — MUST precede heavy imports; silences TF/absl logs

import time
import urllib.request
from collections import deque
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision


# Configuration

EAR_THRESHOLD = 0.21           # below this the eye counts as closed
CONSECUTIVE_CLOSED_FRAMES = 1  # min closed frames per blink (1 catches fast blinks at low FPS)

RIGHT_EYE = [33, 160, 158, 133, 153, 144]
LEFT_EYE = [362, 385, 387, 263, 373, 380]

MODEL_PATH = Path("face_landmarker.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)


# Model

def create_landmarker():
    if not MODEL_PATH.exists():
        print("Downloading face landmark model (~4 MB)...", flush=True)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)

    options = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(MODEL_PATH)),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
    )
    return vision.FaceLandmarker.create_from_options(options)


# Eye Aspect Ratio

def eye_aspect_ratio(landmarks, eye_indices, frame_width, frame_height):
    points = [
        np.array([landmarks[i].x * frame_width, landmarks[i].y * frame_height])
        for i in eye_indices
    ]
    p1, p2, p3, p4, p5, p6 = points

    vertical = np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)
    horizontal = np.linalg.norm(p1 - p4)
    return vertical / (2.0 * horizontal)


# Live loop

def main():
    landmarker = create_landmarker()

    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise RuntimeError("Could not open the webcam.")

    blink_count = 0
    closed_frames = 0
    recent_ears = deque(maxlen=15)
    start_time = time.monotonic()
    print("Blink at the camera. Press ESC to quit.")

    while True:
        ok, frame = camera.read()
        if not ok:
            break

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        timestamp_ms = int((time.monotonic() - start_time) * 1000)
        result = landmarker.detect_for_video(mp_image, timestamp_ms)

        ear = None
        if result.face_landmarks:
            landmarks = result.face_landmarks[0]
            height, width = frame.shape[:2]
            right = eye_aspect_ratio(landmarks, RIGHT_EYE, width, height)
            left = eye_aspect_ratio(landmarks, LEFT_EYE, width, height)
            ear = (right + left) / 2.0
            recent_ears.append(ear)

            if ear < EAR_THRESHOLD:
                closed_frames += 1
            else:
                if closed_frames >= CONSECUTIVE_CLOSED_FRAMES:
                    blink_count += 1
                closed_frames = 0

        display = cv2.flip(frame, 1)
        ear_label = f"EAR: {ear:.3f}" if ear is not None else "No face"
        recent_min = f"min(15f): {min(recent_ears):.3f}" if recent_ears else "min(15f): --"
        cv2.putText(display, ear_label, (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        cv2.putText(display, recent_min, (20, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
        cv2.putText(display, f"Blinks: {blink_count}", (20, 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        cv2.imshow("Liveness - EAR (ESC to quit)", display)

        if cv2.waitKey(1) & 0xFF == 27:
            break

    camera.release()
    cv2.destroyAllWindows()
    landmarker.close()


if __name__ == "__main__":
    main()
