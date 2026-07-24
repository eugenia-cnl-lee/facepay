"""Liveness detection — random challenge-response (blink or head-turn).

Uses the MediaPipe Tasks FaceLandmarker (the legacy `mp.solutions` API was
removed in mediapipe 0.10.35). The landmark model downloads on first run.

Run:  python liveness.py
The program picks a random challenge (blink N times, or turn your head a
direction) with a time limit, then passes or fails you. Press ESC to cancel.
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
YAW_THRESHOLD = 0.25           # how far the head must turn to count (calibrate against the readout)

MIN_BLINKS = 2                 # blink challenge asks for a random count in [MIN_BLINKS, MAX_BLINKS]
MAX_BLINKS = 4
CHALLENGE_SECONDS = 6.0        # time allowed to complete a challenge

RIGHT_EYE = [33, 160, 158, 133, 153, 144]
LEFT_EYE = [362, 385, 387, 263, 373, 380]
NOSE_TIP = 1
LEFT_EYE_OUTER = 33
RIGHT_EYE_OUTER = 263

BRACKET_OPACITY = 0.5    # opacity of the corner brackets drawn around the face
BRACKET_COLOR = (0, 255, 0)
BRACKET_THICKNESS = 1
BRACKET_LENGTH = 15      # length of each corner line (shorter = smaller brackets)
BRACKET_PADDING = 20     # pixels to expand the face box outward

MODEL_PATH = Path("face_landmarker.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/1/face_landmarker.task"
)

WINDOW_NAME = "FacePay"


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


# Landmarks

_last_timestamp_ms = -1


def next_timestamp_ms(start_time):
    global _last_timestamp_ms
    timestamp = int((time.monotonic() - start_time) * 1000)
    if timestamp <= _last_timestamp_ms:
        timestamp = _last_timestamp_ms + 1  # force strictly increasing for MediaPipe
    _last_timestamp_ms = timestamp
    return timestamp


def detect_landmarks(landmarker, frame, start_time):
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect_for_video(mp_image, next_timestamp_ms(start_time))

    if not result.face_landmarks:
        return None
    return result.face_landmarks[0]


def eye_aspect_ratio(landmarks, eye_indices, frame_width, frame_height):
    points = [
        np.array([landmarks[i].x * frame_width, landmarks[i].y * frame_height])
        for i in eye_indices
    ]
    p1, p2, p3, p4, p5, p6 = points

    vertical = np.linalg.norm(p2 - p6) + np.linalg.norm(p3 - p5)
    horizontal = np.linalg.norm(p1 - p4)
    return vertical / (2.0 * horizontal)


def average_ear(landmarks, frame_width, frame_height):
    right = eye_aspect_ratio(landmarks, RIGHT_EYE, frame_width, frame_height)
    left = eye_aspect_ratio(landmarks, LEFT_EYE, frame_width, frame_height)
    return (right + left) / 2.0


def head_yaw(landmarks):
    nose_x = landmarks[NOSE_TIP].x
    left_x = landmarks[LEFT_EYE_OUTER].x
    right_x = landmarks[RIGHT_EYE_OUTER].x

    eye_mid = (left_x + right_x) / 2.0
    eye_dist = abs(right_x - left_x)
    if eye_dist == 0:
        return 0.0

    # negate so the sign matches the mirrored on-screen preview (user's view)
    return -(nose_x - eye_mid) / eye_dist


# Result overlay

def show_result(display, text, color):
    cv2.putText(display, text, (20, 200),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)
    cv2.imshow(WINDOW_NAME, display)
    cv2.waitKey(1500)


def face_bounding_box(landmarks, width, height):
    xs = [(1 - landmark.x) * width for landmark in landmarks]   # mirror x for the flipped preview
    ys = [landmark.y * height for landmark in landmarks]
    return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


def draw_face_overlay(display, landmarks):
    height, width = display.shape[:2]
    x1, y1, x2, y2 = face_bounding_box(landmarks, width, height)
    x1 -= BRACKET_PADDING
    y1 -= BRACKET_PADDING
    x2 += BRACKET_PADDING
    y2 += BRACKET_PADDING
    corner = BRACKET_LENGTH

    overlay = display.copy()
    for cx, cy, dx, dy in [
        (x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1),
    ]:
        cv2.line(overlay, (cx, cy), (cx + dx * corner, cy), BRACKET_COLOR, BRACKET_THICKNESS)
        cv2.line(overlay, (cx, cy), (cx, cy + dy * corner), BRACKET_COLOR, BRACKET_THICKNESS)

    cv2.addWeighted(overlay, BRACKET_OPACITY, display, 1 - BRACKET_OPACITY, 0, display)


def draw_hud(display, title, detail, time_left):
    cv2.putText(display, title, (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
    cv2.putText(display, detail, (20, 80),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    cv2.putText(display, f"Time: {time_left:.1f}s", (20, 120),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)


# Blink challenge

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

        landmarks = detect_landmarks(landmarker, frame, start_time)
        if landmarks is not None:
            height, width = frame.shape[:2]
            ear = average_ear(landmarks, width, height)
            if ear < EAR_THRESHOLD:
                closed_frames += 1
            else:
                if closed_frames >= CONSECUTIVE_CLOSED_FRAMES:
                    blink_count += 1
                closed_frames = 0

        time_left = max(0.0, deadline - time.monotonic())
        display = cv2.flip(frame, 1)
        if landmarks is not None:
            draw_face_overlay(display, landmarks)
        draw_hud(display, f"BLINK {required} TIMES",
                 f"Progress: {min(blink_count, required)}/{required}", time_left)
        cv2.imshow(WINDOW_NAME, display)

        if cv2.waitKey(1) & 0xFF == 27:
            return False
        if blink_count >= required:
            show_result(display, "LIVENESS PASSED", (0, 200, 0))
            return True
        if time_left <= 0:
            show_result(display, "LIVENESS FAILED", (0, 0, 255))
            return False


# Head-turn challenge

def run_turn_challenge(landmarker, camera, start_time):
    direction = random.choice(["LEFT", "RIGHT"])
    deadline = time.monotonic() + CHALLENGE_SECONDS

    print(f"Challenge: turn your head {direction} within {CHALLENGE_SECONDS:.0f} seconds.")

    while True:
        ok, frame = camera.read()
        if not ok:
            return False

        landmarks = detect_landmarks(landmarker, frame, start_time)
        yaw = head_yaw(landmarks) if landmarks is not None else 0.0
        turned = (
            (direction == "RIGHT" and yaw > YAW_THRESHOLD)
            or (direction == "LEFT" and yaw < -YAW_THRESHOLD)
        )

        time_left = max(0.0, deadline - time.monotonic())
        display = cv2.flip(frame, 1)
        if landmarks is not None:
            draw_face_overlay(display, landmarks)
        draw_hud(display, f"TURN YOUR HEAD {direction}", f"yaw: {yaw:+.2f}", time_left)
        cv2.imshow(WINDOW_NAME, display)

        if cv2.waitKey(1) & 0xFF == 27:
            return False
        if turned:
            show_result(display, "LIVENESS PASSED", (0, 200, 0))
            return True
        if time_left <= 0:
            show_result(display, "LIVENESS FAILED", (0, 0, 255))
            return False


# Dispatch

def run_challenge(landmarker, camera, start_time):
    if random.choice(["blink", "turn"]) == "blink":
        return run_blink_challenge(landmarker, camera, start_time)
    return run_turn_challenge(landmarker, camera, start_time)


# Main

def main():
    landmarker = create_landmarker()
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise RuntimeError("Could not open the webcam.")

    start_time = time.monotonic()
    passed = run_challenge(landmarker, camera, start_time)
    print("Result:", "PASS" if passed else "FAIL")

    camera.release()
    cv2.destroyAllWindows()
    landmarker.close()


if __name__ == "__main__":
    main()
