"""FacePay kiosk terminal — a full-screen pay-by-face device screen.

Reuses the real engine (liveness, recognition, risk ladder, PIN, dual control,
encrypted wallet). This file is only the on-screen presentation: amount buttons,
face view, an on-screen PIN keypad, and big approve/decline screens. No console.

Enrol users first with `python facepay.py` (sign-up), then run the terminal.

Run:  python terminal.py   (ESC quits)
"""

import logging_setup  # noqa: F401  — MUST precede heavy imports; silences TF/absl logs

import time
import uuid

import cv2

import approval
import audit
import pin_auth
import rate_limit
import risk
import template_store
import ui
import wallet_store
from facepay import MERCHANT_NAME, OPENING_BALANCE_PENCE, scan_and_identify
from liveness import WINDOW_NAME, create_landmarker, run_challenge

AMOUNTS_PENCE = [400, 7500, 30000]   # £4, £75 (PIN), £300 (dual approval)
RESULT_SECONDS = 2.6


# Small drawing pieces

def symbol(frame, ok, cx, cy, r):
    color = ui.GREEN if ok else ui.RED
    cv2.circle(frame, (cx, cy), r, color, 3, cv2.LINE_AA)
    if ok:
        cv2.line(frame, (cx - r // 2, cy), (cx - r // 8, cy + r // 2), color, 3, cv2.LINE_AA)
        cv2.line(frame, (cx - r // 8, cy + r // 2), (cx + r // 2, cy - r // 3), color, 3, cv2.LINE_AA)
    else:
        cv2.line(frame, (cx - r // 3, cy - r // 3), (cx + r // 3, cy + r // 3), color, 3, cv2.LINE_AA)
        cv2.line(frame, (cx + r // 3, cy - r // 3), (cx - r // 3, cy + r // 3), color, 3, cv2.LINE_AA)


# Screens

def screen_start(camera, mouse):
    while True:
        ok, frame = camera.read()
        if not ok:
            return None
        frame = cv2.flip(frame, 1)
        ui.darken(frame)
        h, w = frame.shape[:2]

        ui.text_center(frame, "FacePay", int(h * 0.16), 1.6, ui.WHITE, 3)
        ui.text_center(frame, "Select amount, then approve with your face", int(h * 0.24), 0.6, ui.GREY, 1)

        buttons = []
        bw, bh, gap = int(w * 0.24), int(h * 0.16), int(w * 0.03)
        total = len(AMOUNTS_PENCE) * bw + (len(AMOUNTS_PENCE) - 1) * gap
        x0 = (w - total) // 2
        y0 = int(h * 0.40)
        for i, amount in enumerate(AMOUNTS_PENCE):
            rect = (x0 + i * (bw + gap), y0, bw, bh)
            ui.button(frame, rect, wallet_store.format_money(amount), ui.WHITE, 1.1)
            buttons.append((rect, amount))

        quit_rect = (int(w * 0.40), int(h * 0.78), int(w * 0.20), int(h * 0.09))
        ui.button(frame, quit_rect, "Quit", ui.GREY, 0.8)

        cv2.imshow(WINDOW_NAME, frame)
        if cv2.waitKey(1) & 0xFF == 27:
            return None

        click = mouse.take()
        for rect, amount in buttons:
            if ui.in_rect(click, rect):
                return amount
        if ui.in_rect(click, quit_rect):
            return None


def screen_result(camera, ok, big, small=""):
    deadline = time.monotonic() + RESULT_SECONDS
    while time.monotonic() < deadline:
        got, frame = camera.read()
        if not got:
            break
        frame = cv2.flip(frame, 1)
        ui.darken(frame, 0.72)
        h, w = frame.shape[:2]
        symbol(frame, ok, w // 2, int(h * 0.34), int(h * 0.12))
        ui.text_center(frame, big, int(h * 0.62), 1.3, ui.GREEN if ok else ui.RED, 3)
        if small:
            ui.text_center(frame, small, int(h * 0.72), 0.7, ui.WHITE, 1)
        cv2.imshow(WINDOW_NAME, frame)
        cv2.waitKey(1)


def enter_pin(camera, mouse, prompt):
    entry = ""
    keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "C", "0", "OK"]
    while True:
        ok, frame = camera.read()
        if not ok:
            return None
        frame = cv2.flip(frame, 1)
        ui.darken(frame, 0.7)
        h, w = frame.shape[:2]

        ui.text_center(frame, prompt, int(h * 0.16), 0.9, ui.WHITE, 2)
        ui.text_center(frame, "*" * len(entry) if entry else "____", int(h * 0.28), 1.4, ui.AMBER, 3)

        bw, bh, gap = int(w * 0.12), int(h * 0.12), int(w * 0.02)
        grid_w = 3 * bw + 2 * gap
        x0 = (w - grid_w) // 2
        y0 = int(h * 0.36)
        rects = []
        for i, key in enumerate(keys):
            r, c = divmod(i, 3)
            rect = (x0 + c * (bw + gap), y0 + r * (bh + gap), bw, bh)
            col = ui.GREEN if key == "OK" else (ui.RED if key == "C" else ui.WHITE)
            ui.button(frame, rect, key, col, 1.0)
            rects.append((rect, key))

        cancel_rect = (int(w * 0.02), int(h * 0.02), int(w * 0.14), int(h * 0.08))
        ui.button(frame, cancel_rect, "Cancel", ui.GREY, 0.7)

        cv2.imshow(WINDOW_NAME, frame)
        pressed = cv2.waitKey(1) & 0xFF
        if pressed == 27:
            return None
        if 48 <= pressed <= 57 and len(entry) < 8:
            entry += chr(pressed)

        click = mouse.take()
        if ui.in_rect(click, cancel_rect):
            return None
        for rect, key in rects:
            if ui.in_rect(click, rect):
                if key == "C":
                    entry = ""
                elif key == "OK":
                    if entry:
                        return entry
                elif len(entry) < 8:
                    entry += key


def prompt_screen(camera, mouse, title, subtitle, button_label):
    while True:
        ok, frame = camera.read()
        if not ok:
            return False
        frame = cv2.flip(frame, 1)
        ui.darken(frame, 0.68)
        h, w = frame.shape[:2]
        ui.text_center(frame, title, int(h * 0.30), 1.0, ui.AMBER, 2)
        ui.text_center(frame, subtitle, int(h * 0.40), 0.7, ui.WHITE, 1)
        rect = (int(w * 0.35), int(h * 0.58), int(w * 0.30), int(h * 0.12))
        ui.button(frame, rect, button_label, ui.WHITE, 1.0)
        cv2.imshow(WINDOW_NAME, frame)
        if cv2.waitKey(1) & 0xFF == 27:
            return False
        if ui.in_rect(mouse.take(), rect):
            return True


# Flow steps

def do_stepup(camera, mouse, name):
    if not pin_auth.has_pin(name):
        pin = enter_pin(camera, mouse, "No PIN yet - set one now")
        if not pin:
            return False
        if enter_pin(camera, mouse, "Confirm your new PIN") != pin:
            screen_result(camera, False, "PINs did not match", "Please try again")
            return False
        pin_auth.set_pin(name, pin)
        return True
    for _ in range(3):
        pin = enter_pin(camera, mouse, "Enter your PIN")
        if pin is None:
            return False
        if pin_auth.verify_pin(name, pin):
            return True
        screen_result(camera, False, "Incorrect PIN")
    return False


def do_dual_approval(camera, mouse, landmarker, start_time, payer):
    if not prompt_screen(camera, mouse, "Second approval required",
                         "An authorised approver must look at the camera", "Approver ready"):
        return False, "cancelled"
    if not run_challenge(landmarker, camera, start_time):
        return False, "approver liveness failed"
    approver, _ = scan_and_identify(camera, template_store.load_database(), landmarker, start_time)
    return approval.check(payer, approver)


def charge(name, amount_pence):
    wallet_store.ensure_account(name, "customer", OPENING_BALANCE_PENCE)
    wallet_store.ensure_account(MERCHANT_NAME, "merchant", 0)
    try:
        wallet_store.transfer(uuid.uuid4().hex, name, MERCHANT_NAME, amount_pence)
    except wallet_store.InsufficientFunds:
        return False
    return True


def run_once(camera, landmarker, mouse):
    amount = screen_start(camera, mouse)
    if amount is None:
        return False

    locked, wait = rate_limit.locked_out()
    if locked:
        audit.log("lockout", detail=f"{wait}s remaining")
        screen_result(camera, False, "Temporarily locked", f"Try again in {wait}s")
        return True

    database = template_store.load_database()
    start_time = time.monotonic()

    if not run_challenge(landmarker, camera, start_time):
        rate_limit.record("failure")
        audit.log("liveness_failure")
        screen_result(camera, False, "Liveness failed")
        return True

    name, distance = scan_and_identify(camera, database, landmarker, start_time)
    if name in ("UNKNOWN", "NO_FACE"):
        rate_limit.record("failure")
        audit.log("auth_failure", detail=f"closest={distance:.3f}")
        screen_result(camera, False, "Not recognised", "Enrol with facepay.py first")
        return True

    rate_limit.record("success")
    audit.log("auth_success", name, f"distance={distance:.3f}")

    decision, reason = risk.assess(amount, rate_limit.failure_count())
    if decision in ("pin_stepup", "dual_approval"):
        audit.log("stepup_required", name, reason)
        if not do_stepup(camera, mouse, name):
            audit.log("stepup_failure", name, reason)
            screen_result(camera, False, "Step-up failed")
            return True
        audit.log("stepup_success", name, reason)

    if decision == "dual_approval":
        approved, why = do_dual_approval(camera, mouse, landmarker, start_time, name)
        audit.log("dual_approval_granted" if approved else "dual_approval_denied", name, why)
        if not approved:
            screen_result(camera, False, "Approval declined", why)
            return True

    if not charge(name, amount):
        audit.log("payment_declined", name, "insufficient funds")
        screen_result(camera, False, "Declined", "Insufficient funds")
        return True

    audit.log("payment", name, f"{wallet_store.format_money(amount)} to {MERCHANT_NAME}")
    balance = wallet_store.get_balance(name)
    screen_result(camera, True, f"Approved  {wallet_store.format_money(amount)}",
                  f"{name} - balance {wallet_store.format_money(balance)}")
    return True


def main():
    template_store.load_database()
    landmarker = create_landmarker()
    camera = cv2.VideoCapture(0)
    if not camera.isOpened():
        raise RuntimeError("Could not open the webcam.")

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(WINDOW_NAME, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    mouse = ui.Mouse(WINDOW_NAME)

    try:
        while run_once(camera, landmarker, mouse):
            pass
    finally:
        camera.release()
        cv2.destroyAllWindows()
        landmarker.close()


if __name__ == "__main__":
    main()
