"""FacePay — end-to-end demo: prove liveness, identify, then (simulated) pay.

Flow:
  1. Enrol known faces from known/.
  2. Liveness challenge (blink or head-turn) — the gate.
  3. On pass, scan and identify the face.
  4. If unknown, offer live multi-frame enrolment.
  5. On a match, run a simulated payment (real payment is Tier 4).

Run:  python facepay.py
"""

import logging_setup  # noqa: F401  — MUST precede heavy imports; silences TF/absl logs

import time
from pathlib import Path

import cv2

from liveness import (
    WINDOW_NAME,
    create_landmarker,
    detect_landmarks,
    draw_face_overlay,
    run_challenge,
)
import template_store
from recognition import create_embedding, identify_face


# Configuration

SCAN_PATH = Path("scan.jpg")
SCAN_ATTEMPTS = 40
SCAN_RETRY_GAP_SECONDS = 0.1
ENROL_FRAMES = 5
ENROL_FRAME_GAP_SECONDS = 0.4
MAX_ENROL_ATTEMPTS = 30
PAYMENT_AMOUNT = 4.00


# Capture

def capture_frame(camera):
    ok, frame = camera.read()
    if not ok:
        raise RuntimeError("Failed to read from the webcam.")
    return frame


def scan_and_identify(camera, database, landmarker, start_time):
    for _ in range(SCAN_ATTEMPTS):
        frame = capture_frame(camera)

        display = cv2.flip(frame, 1)
        landmarks = detect_landmarks(landmarker, frame, start_time)
        if landmarks is not None:
            draw_face_overlay(display, landmarks)
        cv2.putText(display, "Look at the camera", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        cv2.imshow(WINDOW_NAME, display)
        cv2.waitKey(1)

        cv2.imwrite(str(SCAN_PATH), frame)
        try:
            return identify_face(SCAN_PATH, database)
        except ValueError:
            time.sleep(SCAN_RETRY_GAP_SECONDS)
        finally:
            SCAN_PATH.unlink(missing_ok=True)   # no raw image retained

    return "NO_FACE", float("inf")


# Live enrolment

def enrol_live(camera, database, landmarker, start_time):
    name = input("New user — enter your name: ").strip().replace(" ", "_")
    if not name:
        print("No name given; enrolment cancelled.")
        return

    database.pop(name, None)   # clean re-enrolment
    print(f"Capturing {ENROL_FRAMES} frames — look at the camera and move your head slightly...")

    embeddings = []
    attempts = 0
    while len(embeddings) < ENROL_FRAMES and attempts < MAX_ENROL_ATTEMPTS:
        attempts += 1
        frame = capture_frame(camera)

        display = cv2.flip(frame, 1)
        landmarks = detect_landmarks(landmarker, frame, start_time)
        if landmarks is not None:
            draw_face_overlay(display, landmarks)
        cv2.putText(display, f"Enrolling {name}: {len(embeddings)}/{ENROL_FRAMES}", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        cv2.imshow(WINDOW_NAME, display)
        cv2.waitKey(1)

        cv2.imwrite(str(SCAN_PATH), frame)
        try:
            embedding = create_embedding(SCAN_PATH)
        except ValueError:
            continue
        finally:
            SCAN_PATH.unlink(missing_ok=True)   # no raw image retained

        embeddings.append(embedding)
        time.sleep(ENROL_FRAME_GAP_SECONDS)

    if embeddings:
        template_store.enrol_identity(name, embeddings)
        database[name] = embeddings
        print(f"Enrolled {name}: {len(embeddings)} encrypted template(s) stored, no raw image kept.")
    else:
        print("Enrolment failed — no clear face captured.")


# Payment (simulated placeholder for Tier 4)

def take_payment(name):
    print(f"[PAYMENT] Charging GBP {PAYMENT_AMOUNT:.2f} from {name}'s wallet... done (simulated).")


# Main

def cleanup(camera, landmarker):
    camera.release()
    cv2.destroyAllWindows()
    landmarker.close()


def main():
    print("Loading enrolled faces from the encrypted store...")
    database = template_store.load_database()
    print(f"{len(database)} enrolled identities.\n")

    landmarker = create_landmarker()
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise RuntimeError("Could not open the webcam.")

    start_time = time.monotonic()

    print("Step 1 - prove you are live.")
    if not run_challenge(landmarker, camera, start_time):
        print("Liveness failed. Access denied.")
        cleanup(camera, landmarker)
        return
    print("Liveness passed.\n")

    print("Step 2 - identifying...")
    name, distance = scan_and_identify(camera, database, landmarker, start_time)

    if name == "NO_FACE":
        print("No face detected. Please re-run and face the camera after the challenge.")
    elif name == "UNKNOWN":
        print(f"Not recognised (closest distance {distance:.3f}).")
        if input("Enrol as a new user? [y/N]: ").strip().lower() == "y":
            enrol_live(camera, database, landmarker, start_time)
    else:
        print(f"Recognised: {name} (distance {distance:.3f}).")
        take_payment(name)

    cleanup(camera, landmarker)


if __name__ == "__main__":
    main()
