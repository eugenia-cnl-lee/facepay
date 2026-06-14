"""FacePay — end-to-end app: rate-limit, liveness, identify, risk-based pay.

Flow:
  1. Load enrolled templates from the encrypted store; deny if rate-limited.
  2. Liveness challenge (blink or head-turn) — the gate.
  3. Scan and identify the face (offer live enrolment if unknown).
  4. Risk-based step-up: higher-risk payments require a PIN on top of the face.
  5. Charge the wallet (atomic, idempotent) and write to the audit log.

Run:  python facepay.py
"""

import logging_setup  # noqa: F401  — MUST precede heavy imports; silences TF/absl logs

import time
import uuid
from pathlib import Path

import cv2

from liveness import (
    WINDOW_NAME,
    create_landmarker,
    detect_landmarks,
    draw_face_overlay,
    run_challenge,
)
import approval
import audit
import pin_auth
import rate_limit
import risk
import template_store
import wallet_store
from recognition import create_embedding, identify_face


# Configuration

SCAN_PATH = Path("scan.jpg")
SCAN_ATTEMPTS = 40
SCAN_RETRY_GAP_SECONDS = 0.1
ENROL_FRAMES = 5
ENROL_FRAME_GAP_SECONDS = 0.4
MAX_ENROL_ATTEMPTS = 30

MERCHANT_NAME = "FacePay_Merchant"
OPENING_BALANCE_PENCE = 50000   # a new customer starts with £500.00 (demo)
PAYMENT_AMOUNT_PENCE = 400     # £4.00 per purchase


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
        audit.log("enrol", name, f"{len(embeddings)} templates")
        print(f"Enrolled {name}: {len(embeddings)} encrypted template(s) stored, no raw image kept.")
        if input("Is this person an authorised approver (e.g. a manager)? [y/N]: ").strip().lower() == "y":
            approval.mark_approver(name)
            print(f"{name} can now approve high-value payments.")
    else:
        print("Enrolment failed — no clear face captured.")


# Risk-based step-up

def prompt_amount():
    raw = input("Amount to pay in GBP [4.00]: ").strip()
    if not raw:
        return PAYMENT_AMOUNT_PENCE
    try:
        return max(0, round(float(raw) * 100))
    except ValueError:
        return PAYMENT_AMOUNT_PENCE


def step_up_pin(name):
    if not pin_auth.has_pin(name):
        print("No PIN set yet — choose one now for high-value payments.")
        pin = input("Set a PIN: ").strip()
        if not pin:
            return False
        if input("Confirm your PIN: ").strip() != pin:
            print("PINs did not match. Cancelled.")
            return False
        pin_auth.set_pin(name, pin)
        print("PIN set.")
        return True

    for _ in range(3):
        if pin_auth.verify_pin(name, input("Higher-risk payment — enter your PIN: ").strip()):
            return True
        print("Incorrect PIN.")
    return False


def dual_approve(camera, database, landmarker, start_time, payer):
    print("This payment needs a second approver. Approver, look at the camera.")
    input("Press Enter when the approver is ready...")
    if not run_challenge(landmarker, camera, start_time):
        return False, "approver liveness failed"
    approver, _ = scan_and_identify(camera, database, landmarker, start_time)
    return approval.check(payer, approver)


# Payment

def take_payment(name, amount_pence):
    wallet_store.ensure_account(name, "customer", OPENING_BALANCE_PENCE)
    wallet_store.ensure_account(MERCHANT_NAME, "merchant", 0)

    key = uuid.uuid4().hex   # one idempotency key per purchase; a retry charges once
    try:
        wallet_store.transfer(key, name, MERCHANT_NAME, amount_pence)
    except wallet_store.InsufficientFunds:
        balance = wallet_store.get_balance(name)
        print(f"[PAYMENT] Declined — insufficient funds (balance {wallet_store.format_money(balance)}).")
        audit.log("payment_declined", name, "insufficient funds")
        return

    balance = wallet_store.get_balance(name)
    amount = wallet_store.format_money(amount_pence)
    print(f"[PAYMENT] {amount} charged from {name} to {MERCHANT_NAME}. "
          f"New balance: {wallet_store.format_money(balance)}.")
    audit.log("payment", name, f"{amount} to {MERCHANT_NAME}")


# Main

def cleanup(camera, landmarker):
    camera.release()
    cv2.destroyAllWindows()
    landmarker.close()


def main():
    print("Loading enrolled faces from the encrypted store...")
    database = template_store.load_database()
    print(f"{len(database)} enrolled identities.\n")

    locked, wait = rate_limit.locked_out()
    if locked:
        audit.log("lockout", detail=f"{wait}s remaining")
        print(f"Locked: too many failed attempts. Try again in {wait}s. (anti hill-climbing)")
        return

    amount_pence = prompt_amount()

    landmarker = create_landmarker()
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise RuntimeError("Could not open the webcam.")

    start_time = time.monotonic()

    print("Step 1 - prove you are live.")
    if not run_challenge(landmarker, camera, start_time):
        print("Liveness failed. Access denied.")
        rate_limit.record("failure")
        audit.log("liveness_failure")
        cleanup(camera, landmarker)
        return
    print("Liveness passed.\n")

    print("Step 2 - identifying...")
    name, distance = scan_and_identify(camera, database, landmarker, start_time)

    if name == "NO_FACE":
        print("No face detected. Please re-run and face the camera after the challenge.")
    elif name == "UNKNOWN":
        rate_limit.record("failure")
        audit.log("auth_failure", detail=f"closest={distance:.3f}")
        print(f"Not recognised (closest distance {distance:.3f}).")
        if input("Enrol as a new user? [y/N]: ").strip().lower() == "y":
            enrol_live(camera, database, landmarker, start_time)
    else:
        rate_limit.record("success")
        audit.log("auth_success", name, f"distance={distance:.3f}")
        print(f"Recognised: {name} (distance {distance:.3f}).")

        decision, reason = risk.assess(amount_pence, rate_limit.failure_count())

        if decision in ("pin_stepup", "dual_approval"):
            print(f"Step-up required ({reason}).")
            audit.log("stepup_required", name, reason)
            if not step_up_pin(name):
                print("Step-up failed. Payment cancelled.")
                audit.log("stepup_failure", name, reason)
                cleanup(camera, landmarker)
                return
            audit.log("stepup_success", name, reason)

        if decision == "dual_approval":
            approved, why = dual_approve(camera, database, landmarker, start_time, name)
            audit.log("dual_approval_granted" if approved else "dual_approval_denied", name, why)
            if not approved:
                print(f"Dual approval failed: {why}. Payment cancelled.")
                cleanup(camera, landmarker)
                return
            print("Dual approval granted.")

        take_payment(name, amount_pence)

    cleanup(camera, landmarker)


if __name__ == "__main__":
    main()
