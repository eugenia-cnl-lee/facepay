"""FacePay desktop terminal — a customtkinter GUI over the real engine.

A payment-device app: type the amount on the keypad, press Approve, and the
camera panel runs the liveness challenge + face scan. Higher amounts add a PIN
(same keypad) or a second approver. Recognition runs in a background thread so
the window never freezes. Enrol users first with `python facepay.py`.

Run:  python app.py
"""

import logging_setup  # noqa: F401  — MUST precede heavy imports; silences TF/absl logs

import threading
import time
import uuid
from pathlib import Path

import cv2
import customtkinter as ctk
from PIL import Image

import approval
import audit
import pin_auth
import rate_limit
import risk
import template_store
import wallet_store
from liveness import Challenge, create_landmarker, detect_landmarks, draw_face_overlay
from recognition import identify_face

MERCHANT_NAME = "FacePay_Merchant"
OPENING_BALANCE_PENCE = 50000
SCAN_PATH = Path("scan.jpg")
CAM_W, CAM_H = 400, 240
RESULT_MS = 2600

money = wallet_store.format_money

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")


class FacePayApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("FacePay")
        self.geometry("440x820")
        self.resizable(False, False)

        self.phase = "IDLE"
        self.entry = ""
        self.mode = "amount"          # 'amount' or 'pin'
        self.pin_stage = None         # 'verify' / 'set' / 'confirm'
        self._pin_first = ""
        self.amount_pence = 0
        self.payer = None
        self.decision = "allow"
        self.challenge = None
        self._scan = None
        self._last_frame = None

        self._build_ui()

        template_store.load_database()
        self.landmarker = create_landmarker()
        self.start_time = time.monotonic()
        self.camera = cv2.VideoCapture(0)
        if not self.camera.isOpened():
            self.set_status("Could not open the webcam.", "red")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._reset()
        self.tick()

    # UI

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(16, 8))
        ctk.CTkLabel(header, text="FacePay", font=("", 22, "bold")).pack(side="left")
        ctk.CTkLabel(header, text="● ready", text_color="#5cd08a").pack(side="right")

        self.cam_label = ctk.CTkLabel(self, text="", width=CAM_W, height=CAM_H,
                                      fg_color="#0c0d10", corner_radius=12)
        self.cam_label.pack(padx=20, pady=6)

        self.status = ctk.CTkLabel(self, text="", font=("", 15), wraplength=400)
        self.status.pack(padx=20, pady=(6, 2))

        self.display = ctk.CTkLabel(self, text="£0.00", font=("", 40, "bold"))
        self.display.pack(padx=20, pady=(4, 8))

        pad = ctk.CTkFrame(self, fg_color="transparent")
        pad.pack(padx=20)
        keys = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["C", "0", "DEL"]]
        for r, row in enumerate(keys):
            for c, key in enumerate(row):
                ctk.CTkButton(pad, text=key, width=118, height=58, font=("", 20),
                              fg_color="#23252b", hover_color="#2f3238",
                              command=lambda k=key: self.on_key(k)).grid(row=r, column=c, padx=5, pady=5)

        self.approve = ctk.CTkButton(self, text="Approve with face", height=56, font=("", 17, "bold"),
                                     command=self.on_approve)
        self.approve.pack(fill="x", padx=20, pady=16)

    # State helpers

    def set_status(self, text, color="#e9eaed"):
        self.status.configure(text=text, text_color=color)

    def update_display(self):
        if self.mode == "amount":
            self.display.configure(text=money(int(self.entry or "0")))
        else:
            self.display.configure(text=("*" * len(self.entry)) if self.entry else "----")

    def _reset(self):
        self.phase = "IDLE"
        self.entry = ""
        self.mode = "amount"
        self.pin_stage = None
        self.payer = None
        self.approve.configure(text="Approve with face")
        self.set_status("Enter an amount, then approve with your face.")
        self.update_display()

    def _show_result(self, ok, big, small=""):
        self.phase = "RESULT"
        self.set_status(f"{big}\n{small}" if small else big, "#5cd08a" if ok else "#e26d6d")
        self.after(RESULT_MS, self._reset)

    # Camera loop

    def tick(self):
        ok, frame = self.camera.read()
        if ok:
            self._last_frame = frame
            display = cv2.flip(frame, 1)
            if self.phase in ("LIVENESS", "DUAL_LIVENESS"):
                landmarks = detect_landmarks(self.landmarker, frame, self.start_time)
                if landmarks is not None:
                    draw_face_overlay(display, landmarks)
                self.challenge.update(landmarks, frame.shape[1], frame.shape[0])
                self.set_status(f"{self.challenge.status}   {self.challenge.seconds_left:.0f}s", "#f0b64a")
                if self.challenge.done:
                    self._liveness_done()
            self._render(display)
            if self.phase in ("SCANNING", "DUAL_SCANNING") and self._scan is not None:
                self._scan_done()
        self.after(30, self.tick)

    def _render(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        image = ctk.CTkImage(light_image=pil, dark_image=pil, size=(CAM_W, CAM_H))
        self.cam_label.configure(image=image)
        self.cam_label._image = image

    # Keypad + approve

    def on_key(self, key):
        if self.phase not in ("IDLE", "PIN"):
            return
        if key == "C":
            self.entry = ""
        elif key == "DEL":
            self.entry = self.entry[:-1]
        elif len(self.entry) < (8 if self.mode == "pin" else 6):
            self.entry += key
        self.update_display()

    def on_approve(self):
        if self.phase == "IDLE":
            self._start_payment()
        elif self.phase == "PIN":
            self._submit_pin()
        elif self.phase == "DUAL_PROMPT":
            self.phase = "DUAL_LIVENESS"
            self.challenge = Challenge()

    # Flow

    def _start_payment(self):
        self.amount_pence = int(self.entry or "0")
        if self.amount_pence <= 0:
            return
        locked, wait = rate_limit.locked_out()
        if locked:
            audit.log("lockout", detail=f"{wait}s remaining")
            self._show_result(False, "Temporarily locked", f"Try again in {wait}s")
            return
        self.phase = "LIVENESS"
        self.challenge = Challenge()

    def _liveness_done(self):
        if not self.challenge.passed:
            if self.phase == "LIVENESS":
                rate_limit.record("failure")
                audit.log("liveness_failure")
            self._show_result(False, "Liveness failed")
            return
        self.phase = "SCANNING" if self.phase == "LIVENESS" else "DUAL_SCANNING"
        self.set_status("Identifying...", "#f0b64a")
        self._start_scan(self._last_frame)

    def _start_scan(self, frame):
        self._scan = None

        def work():
            cv2.imwrite(str(SCAN_PATH), frame)
            try:
                result = identify_face(SCAN_PATH, template_store.load_database())
            except ValueError:
                result = ("NO_FACE", float("inf"))
            finally:
                SCAN_PATH.unlink(missing_ok=True)
            self._scan = result

        threading.Thread(target=work, daemon=True).start()

    def _scan_done(self):
        name, distance = self._scan
        self._scan = None

        if self.phase == "SCANNING":
            if name in ("UNKNOWN", "NO_FACE"):
                rate_limit.record("failure")
                audit.log("auth_failure", detail=f"closest={distance:.3f}")
                self._show_result(False, "Not recognised", "Enrol with facepay.py first")
                return
            rate_limit.record("success")
            audit.log("auth_success", name, f"distance={distance:.3f}")
            self.payer = name
            self.decision, reason = risk.assess(self.amount_pence, rate_limit.failure_count())
            if self.decision == "allow":
                self._charge()
            else:
                audit.log("stepup_required", name, reason)
                self._enter_pin()
        else:  # DUAL_SCANNING
            ok, why = approval.check(self.payer, name)
            audit.log("dual_approval_granted" if ok else "dual_approval_denied", self.payer, why)
            if ok:
                self._charge()
            else:
                self._show_result(False, "Approval declined", why)

    def _enter_pin(self):
        self.phase = "PIN"
        self.mode = "pin"
        self.entry = ""
        self.pin_stage = "verify" if pin_auth.has_pin(self.payer) else "set"
        self.approve.configure(text="Confirm PIN")
        self.set_status("Enter your PIN" if self.pin_stage == "verify" else "No PIN yet - set one now")
        self.update_display()

    def _submit_pin(self):
        if not self.entry:
            return
        if self.pin_stage == "verify":
            if pin_auth.verify_pin(self.payer, self.entry):
                self._after_pin()
            else:
                self.entry = ""
                self.set_status("Incorrect PIN. Try again.", "#e26d6d")
                self.update_display()
        elif self.pin_stage == "set":
            self._pin_first = self.entry
            self.entry = ""
            self.pin_stage = "confirm"
            self.set_status("Confirm your new PIN")
            self.update_display()
        else:  # confirm
            if self.entry == self._pin_first:
                pin_auth.set_pin(self.payer, self.entry)
                self._after_pin()
            else:
                self.entry = ""
                self.pin_stage = "set"
                self.set_status("PINs did not match. Set one again.", "#e26d6d")
                self.update_display()

    def _after_pin(self):
        audit.log("stepup_success", self.payer, self.decision)
        self.mode = "amount"
        self.approve.configure(text="Approve with face")
        if self.decision == "dual_approval":
            self.phase = "DUAL_PROMPT"
            self.approve.configure(text="Approver ready")
            self.set_status("Second approval needed. Approver, look at the camera, then press Approver ready.",
                            "#f0b64a")
        else:
            self._charge()

    def _charge(self):
        wallet_store.ensure_account(self.payer, "customer", OPENING_BALANCE_PENCE)
        wallet_store.ensure_account(MERCHANT_NAME, "merchant", 0)
        try:
            wallet_store.transfer(uuid.uuid4().hex, self.payer, MERCHANT_NAME, self.amount_pence)
        except wallet_store.InsufficientFunds:
            balance = wallet_store.get_balance(self.payer)
            audit.log("payment_declined", self.payer, "insufficient funds")
            self._show_result(False, "Declined - insufficient funds", f"Balance {money(balance)}")
            return
        audit.log("payment", self.payer, f"{money(self.amount_pence)} to {MERCHANT_NAME}")
        balance = wallet_store.get_balance(self.payer)
        self._show_result(True, f"Approved  {money(self.amount_pence)}", f"{self.payer} - balance {money(balance)}")

    def _on_close(self):
        self.camera.release()
        self.landmarker.close()
        self.destroy()


if __name__ == "__main__":
    FacePayApp().mainloop()
