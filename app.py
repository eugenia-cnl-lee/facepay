"""FacePay desktop terminal — a customtkinter GUI over the real engine.

A payment-device app: type the amount on the keypad, press Approve, and the
camera panel (the terminal "screen") runs the liveness challenge + face scan.
Higher amounts add a PIN (same keypad) or a second approver. Recognition runs
in a background thread so the window never freezes. Enrol users first with
`python facepay.py`.

Run:  python app.py
"""

import logging_setup  # noqa: F401  — MUST precede heavy imports; silences TF/absl logs

import threading
import time
import uuid
from pathlib import Path

import cv2
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

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
CAM_W, CAM_H = 400, 250
RESULT_MS = 2600

money = wallet_store.format_money

# Device palette (light-grey metallic body, recessed dark screen)
BODY = "#c3c8ce"
BODY_EDGE = "#8f959d"
BEZEL = "#2b2e33"
BEZEL_EDGE = "#565b62"
SCREEN = "#0a0c0e"
LCD_BG = "#0c140d"
LCD_EDGE = "#39513c"
LCD_TEXT = "#6cff9e"
KEY = "#dfe3e8"
KEY_HOVER = "#eef1f4"
KEY_EDGE = "#a7adb5"
KEY_TEXT = "#20242a"
ACCENT = "#2f7d4f"
ACCENT_HOVER = "#3a9660"
INK = "#20242a"
SLOGAN_INK = "#4a4f56"

SERIF = "Times New Roman"

# HUD (on-screen) colours are RGB (drawn on the camera image with PIL)
HUD_GREEN = (120, 255, 160)
HUD_AMBER = (255, 190, 80)
HUD_RED = (255, 110, 110)

ctk.set_appearance_mode("light")


def _mono(size):
    for name in ("consola.ttf", "C:/Windows/Fonts/consola.ttf", "cour.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class FacePayApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("FacePay")
        self.geometry("460x880")
        self.resizable(False, False)
        self.configure(fg_color=BODY)

        self.phase = "IDLE"
        self.entry = ""
        self.mode = "amount"
        self.pin_stage = None
        self._pin_first = ""
        self.amount_pence = 0
        self.payer = None
        self.decision = "allow"
        self.challenge = None
        self._scan = None
        self._last_frame = None
        self.hud_text = ""
        self.hud_color = HUD_GREEN

        self.font_hud = _mono(23)
        self.font_hud_small = _mono(15)

        self._build_ui()

        template_store.load_database()
        self.landmarker = create_landmarker()
        self.start_time = time.monotonic()
        self.camera = cv2.VideoCapture(0)
        if not self.camera.isOpened():
            self.set_status("CAMERA UNAVAILABLE", HUD_RED)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._reset()
        self.tick()

    # UI

    def _build_ui(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(18, 2))
        ctk.CTkLabel(head, text="FacePay", font=(SERIF, 30, "bold"), text_color=INK).pack()
        ctk.CTkLabel(head, text="— no.1 top security transactions —",
                     font=(SERIF, 14, "italic"), text_color=SLOGAN_INK).pack(pady=(0, 6))

        bezel = ctk.CTkFrame(self, fg_color=BEZEL, corner_radius=16,
                             border_width=3, border_color=BEZEL_EDGE)
        bezel.pack(padx=24, pady=8)
        self.cam_label = ctk.CTkLabel(bezel, text="", width=CAM_W, height=CAM_H,
                                      fg_color=SCREEN, corner_radius=8)
        self.cam_label.pack(padx=10, pady=10)

        lcd = ctk.CTkFrame(self, fg_color=LCD_BG, corner_radius=10,
                           border_width=2, border_color=LCD_EDGE)
        lcd.pack(padx=24, pady=(10, 8), fill="x")
        self.display = ctk.CTkLabel(lcd, text="£0.00", font=("Consolas", 36, "bold"),
                                    text_color=LCD_TEXT)
        self.display.pack(padx=16, pady=8)

        pad = ctk.CTkFrame(self, fg_color="transparent")
        pad.pack(padx=24, pady=4)
        keys = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["C", "0", "DEL"]]
        for r, row in enumerate(keys):
            for c, key in enumerate(row):
                ctk.CTkButton(pad, text=key, width=124, height=56, corner_radius=10,
                              font=(SERIF, 22, "bold"), fg_color=KEY, hover_color=KEY_HOVER,
                              text_color=KEY_TEXT, border_width=2, border_color=KEY_EDGE,
                              command=lambda k=key: self.on_key(k)).grid(row=r, column=c, padx=6, pady=6)

        self.approve = ctk.CTkButton(self, text="Approve with face", height=58, corner_radius=12,
                                     font=(SERIF, 19, "bold"), fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                     border_width=2, border_color=BODY_EDGE, command=self.on_approve)
        self.approve.pack(fill="x", padx=24, pady=(12, 18))

    # State helpers

    def set_status(self, text, color=HUD_GREEN):
        self.hud_text = text
        self.hud_color = color

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
        self.set_status("ENTER AMOUNT")
        self.update_display()

    def _show_result(self, ok, big, small=""):
        self.phase = "RESULT"
        self.set_status(f"{big}\n{small}" if small else big, HUD_GREEN if ok else HUD_RED)
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
                self.set_status(f"{self.challenge.status}   {self.challenge.seconds_left:.0f}s", HUD_AMBER)
                if self.challenge.done:
                    self._liveness_done()
            self._render(display)
            if self.phase in ("SCANNING", "DUAL_SCANNING") and self._scan is not None:
                self._scan_done()
        self.after(30, self.tick)

    def _render(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        draw = ImageDraw.Draw(pil)
        width, height = pil.size

        draw.text((14, 10), "FACEPAY // SECURE", font=self.font_hud_small, fill=HUD_GREEN)
        if self.hud_text:
            lines = self.hud_text.split("\n")
            y = height - 16 - len(lines) * 28
            for line in lines:
                box = draw.textbbox((0, 0), line, font=self.font_hud)
                draw.text(((width - (box[2] - box[0])) // 2, y), line, font=self.font_hud, fill=self.hud_color)
                y += 28

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
            self._show_result(False, "TEMPORARILY LOCKED", f"try again in {wait}s")
            return
        self.phase = "LIVENESS"
        self.challenge = Challenge()

    def _liveness_done(self):
        if not self.challenge.passed:
            if self.phase == "LIVENESS":
                rate_limit.record("failure")
                audit.log("liveness_failure")
            self._show_result(False, "LIVENESS FAILED")
            return
        self.phase = "SCANNING" if self.phase == "LIVENESS" else "DUAL_SCANNING"
        self.set_status("IDENTIFYING...", HUD_AMBER)
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
                self._show_result(False, "NOT RECOGNISED", "enrol with facepay.py first")
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
        else:
            ok, why = approval.check(self.payer, name)
            audit.log("dual_approval_granted" if ok else "dual_approval_denied", self.payer, why)
            if ok:
                self._charge()
            else:
                self._show_result(False, "APPROVAL DECLINED", why)

    def _enter_pin(self):
        self.phase = "PIN"
        self.mode = "pin"
        self.entry = ""
        self.pin_stage = "verify" if pin_auth.has_pin(self.payer) else "set"
        self.approve.configure(text="Confirm PIN")
        self.set_status("ENTER YOUR PIN" if self.pin_stage == "verify" else "NO PIN YET - SET ONE", HUD_AMBER)
        self.update_display()

    def _submit_pin(self):
        if not self.entry:
            return
        if self.pin_stage == "verify":
            if pin_auth.verify_pin(self.payer, self.entry):
                self._after_pin()
            else:
                self.entry = ""
                self.set_status("INCORRECT PIN", HUD_RED)
                self.update_display()
        elif self.pin_stage == "set":
            self._pin_first = self.entry
            self.entry = ""
            self.pin_stage = "confirm"
            self.set_status("CONFIRM YOUR PIN", HUD_AMBER)
            self.update_display()
        else:
            if self.entry == self._pin_first:
                pin_auth.set_pin(self.payer, self.entry)
                self._after_pin()
            else:
                self.entry = ""
                self.pin_stage = "set"
                self.set_status("PINS DID NOT MATCH", HUD_RED)
                self.update_display()

    def _after_pin(self):
        audit.log("stepup_success", self.payer, self.decision)
        self.mode = "amount"
        self.approve.configure(text="Approve with face")
        if self.decision == "dual_approval":
            self.phase = "DUAL_PROMPT"
            self.approve.configure(text="Approver ready")
            self.set_status("SECOND APPROVAL NEEDED\napprover, look and press ready", HUD_AMBER)
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
            self._show_result(False, "DECLINED - LOW FUNDS", f"balance {money(balance)}")
            return
        audit.log("payment", self.payer, f"{money(self.amount_pence)} to {MERCHANT_NAME}")
        balance = wallet_store.get_balance(self.payer)
        self._show_result(True, f"APPROVED  {money(self.amount_pence)}", f"{self.payer} - balance {money(balance)}")

    def _on_close(self):
        self.camera.release()
        self.landmarker.close()
        self.destroy()


if __name__ == "__main__":
    FacePayApp().mainloop()
