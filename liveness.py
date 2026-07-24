"""Liveness detection — random blink challenge-response.

Uses the MediaPipe Tasks FaceLandmarker (the legacy `mp.solutions` API was
removed in mediapipe 0.10.35). The landmark model downloads on first run.

Run:  python liveness.py
The program picks a random number of blinks and a time limit, then passes or
fails you. Press ESC to cancel.
"""

import logging_setup  # noqa: F401  — MUST precede heavy imports; silences TF/absl logs

import random
import time
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision


# Configuration

EAR_THRESHOLD = 0.21           # below this the eye counts as closed
CONSECUTIVE_CLOSED_FRAMES = 1  # min closed frames per blink (1 catches fast blinks at low FPS)

MIN_BLINKS = 2                 # challenge asks for a random count in [MIN_BLINKS, MAX_BLINKS]
MAX_BLINKS = 4
CHALLENGE_SECONDS = 6.0        # time allowed to complete the challenge

RIGHT_EYE = [33, 160, 158, 133, 153, 144]
LEFT_EYE = [362, 385, 387, 263, 373, 380]

MODEL_PATH = Path("face_landmarker.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)

WINDOW_NAME = "Liveness challenge (ESC to cancel)"


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


def detect_ear(landmarker, frame, start_time):
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    timestamp_ms = int((time.monotonic() - start_time) * 1000)
    result = landmarker.detect_for_video(mp_image, timestamp_ms)

    if not result.face_landmarks:
        return None

    landmarks = result.face_landmarks[0]
    height, width = frame.shape[:2]
    right = eye_aspect_ratio(landmarks, RIGHT_EYE, width, height)
    left = eye_aspect_ratio(landmarks, LEFT_EYE, width, height)
    return (right + left) / 2.0


# Challenge

def show_result(display, text, color):
    cv2.putText(display, text, (20, 200),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)
    cv2.imshow(WINDOW_NAME, display)
    cv2.waitKey(1500)


def run_blink_challenge(landmarker, camera, start_time):
    required = random.randint(MIN_BLINKS, MAX_BLINKS)
    deadline = time.monotonic() + CHALLENGE_SECONDS
    blink_count = 0
    closed_frames = 0

    print(f"Challenge: blink {required} times in {CHALLENGE_SECONDS:.0f} seconds.")

    while True:
        ok, frame = camera.read()
        if not ok:
            return False

        ear = detect_ear(landmarker, frame, start_time)
        if ear is not None:
            if ear < EAR_THRESHOLD:
                closed_frames += 1
            else:
                if closed_frames >= CONSECUTIVE_CLOSED_FRAMES:
                    blink_count += 1
                closed_frames = 0

        time_left = max(0.0, deadline - time.monotonic())
        display = cv2.flip(frame, 1)
        cv2.putText(display, f"BLINK {required} TIMES", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        cv2.putText(display, f"Progress: {min(blink_count, required)}/{required}", (20, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.putText(display, f"Time: {time_left:.1f}s", (20, 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
        cv2.imshow(WINDOW_NAME, display)

        if cv2.waitKey(1) & 0xFF == 27:
            return False

        if blink_count >= required:
            show_result(display, "LIVENESS PASSED", (0, 200, 0))
            return True
        if time_left <= 0:
            show_result(display, "LIVENESS FAILED", (0, 0, 255))
            return False


# Main

def main():
    landmarker = create_landmarker()
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise RuntimeError("Could not open the webcam.")

    start_time = time.monotonic()
    passed = run_blink_challenge(landmarker, camera, start_time)
    print("Result:", "PASS" if passed else "FAIL")

    camera.release()
    cv2.destroyAllWindows()
    landmarker.close()


if __name__ == "__main__":
    main()
