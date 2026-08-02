"""FacePay — Flask + pywebview desktop shell over the real engine.

A face-authenticated payment terminal with a device/owner lifecycle:

  * BOOT      -> owner logs in (liveness + face); unknown faces can register.
  * SESSION   -> clients pay; each payment is credited to the logged-in owner.
  * ESC       -> confirm -> owner liveness + face -> "Session ended" -> app closes.

The React/Tailwind frontend in `frontend/` is the entire UI. This module:
  * owns the camera + runs the flow in a background thread,
  * streams annotated frames as MJPEG        (GET  /api/video),
  * publishes live state the page polls        (GET  /api/state),
  * accepts commands the page posts            (POST /api/command),
  * serves the built frontend in a frameless native window (pywebview).

Build the frontend first:  cd frontend && npm install && npm run build
Then run:                  python web_app.py
"""

import logging_setup  # noqa: F401  — MUST precede heavy imports; silences TF/absl logs

import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request

import approval
import audit
import pin_auth
import rate_limit
import risk
import template_store
import wallet_store
from liveness import Challenge, create_landmarker, detect_landmarks, draw_face_overlay
from recognition import create_embedding, identify_face

OPENING_BALANCE_PENCE = 50000
SCAN_PATH = Path("scan.jpg")
RESULT_SECONDS = 4.0
CLOSE_SECONDS = 2.2
ENROL_FRAMES = 5
ENROL_FRAME_GAP = 0.4
MAX_ENROL_ATTEMPTS = 30
PORT = 8730
FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

money = wallet_store.format_money


def _blank_jpeg(width=640, height=480):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", frame)
    return buf.tobytes() if ok else b""


class Engine:
    """Device + session + verification flow, driven by one background thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._jpeg = _blank_jpeg()
        self._running = True
        self._last_frame = None
        self._scan = None
        self.window = None  # set by main() so logout can close the app

        template_store.load_database()
        self.landmarker = create_landmarker()
        self.start_time = time.monotonic()
        self.camera = cv2.VideoCapture(0)
        self.camera_ok = self.camera.isOpened()

        self.session_owner = None
        self.session_id = uuid.uuid4().hex[:11].upper()
        self.log = []
        self._clear_transaction()
        threading.Thread(target=self._loop, daemon=True).start()

    # ---- state ---------------------------------------------------------

    def _clear_transaction(self):
        """Reset the per-transaction fields; phase depends on whether logged in."""
        with self._lock:
            self.phase = "IDLE" if self.session_owner else "OWNER_LOGIN"
            self.intent = None
            self.amount_pence = 0
            self.amount_str = ""
            self.prompt = ""
            self.seconds_left = 0.0
            self.challenge = None
            self.payer = None
            self.decision = None
            self.reason = None
            self.pin_mode = None
            self._pin_first = ""
            self.identity = {"match": "PENDING", "confidence": None, "user_id": None, "distance": None}
            self.liveness = "AWAITING"
            self.result = None
            self.enroll_name = None
            self.enroll_progress = 0
        self._scan = None

    def reset(self):
        self._clear_transaction()

    def _event(self, text, event=None, subject=None, detail=None):
        stamp = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.log = (self.log + [f"{stamp}  {text}"])[-16:]
        if event:
            audit.log(event, subject, detail)

    def to_state(self):
        with self._lock:
            owner = self.session_owner
            return {
                "phase": self.phase,
                "intent": self.intent,
                "camera_ok": self.camera_ok,
                "session_id": self.session_id,
                "session_owner": owner,
                "owner_balance": money(wallet_store.get_balance(owner)) if owner else None,
                "amount": self.amount_str,
                "prompt": self.prompt,
                "seconds_left": round(self.seconds_left, 1),
                "identity": dict(self.identity),
                "liveness": self.liveness,
                "risk": {"decision": self.decision, "reason": self.reason,
                         "failures": rate_limit.failure_count()},
                "pin_mode": self.pin_mode,
                "result": dict(self.result) if self.result else None,
                "enroll": {"name": self.enroll_name, "progress": self.enroll_progress, "total": ENROL_FRAMES},
                "audit": list(self.log),
                "model": "FaceNet-512",
                "threshold": 0.25,
            }

    # ---- camera loop ---------------------------------------------------

    def _loop(self):
        while self._running:
            if not self.camera_ok:
                time.sleep(0.1)
                continue
            ok, frame = self.camera.read()
            if not ok:
                time.sleep(0.05)
                continue
            self._last_frame = frame
            display = cv2.flip(frame, 1)

            phase = self.phase
            if phase in ("LIVENESS", "DUAL_LIVENESS"):
                landmarks = detect_landmarks(self.landmarker, frame, self.start_time)
                if landmarks is not None:
                    draw_face_overlay(display, landmarks)
                self.challenge.update(landmarks, frame.shape[1], frame.shape[0])
                with self._lock:
                    self.prompt = self.challenge.status
                    self.seconds_left = self.challenge.seconds_left
                    self.liveness = "ANALYSING"
                if self.challenge.done:
                    self._liveness_done()
            elif phase in ("ENROLLING", "OWNER_ENROLLING"):
                landmarks = detect_landmarks(self.landmarker, frame, self.start_time)
                if landmarks is not None:
                    draw_face_overlay(display, landmarks)
            elif phase in ("SCANNING", "DUAL_SCANNING") and self._scan is not None:
                self._scan_done()

            ok2, buf = cv2.imencode(".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok2:
                with self._frame_lock:
                    self._jpeg = buf.tobytes()
            time.sleep(0.02)

    def frame_bytes(self):
        with self._frame_lock:
            return self._jpeg

    # ---- commands (called from Flask threads) --------------------------

    def owner_login(self):
        if self.phase != "OWNER_LOGIN":
            return
        with self._lock:
            self.intent = "owner_login"
            self.phase = "LIVENESS"
            self.challenge = Challenge()
            self.liveness = "ANALYSING"
        self._event("Owner login — liveness challenge", "owner_login_start")

    def owner_register(self, name):
        if self.phase != "OWNER_ENROLL_OFFER":
            return
        self._begin_enroll(name, as_owner=True)

    def request_logout(self):
        if self.session_owner is None or self.phase != "IDLE":
            return
        with self._lock:
            self.phase = "LOGOUT_CONFIRM"
            self.prompt = f"End this session, {self.session_owner}?"

    def logout_confirm(self):
        if self.phase != "LOGOUT_CONFIRM":
            return
        with self._lock:
            self.intent = "logout"
            self.phase = "LIVENESS"
            self.challenge = Challenge()
            self.liveness = "ANALYSING"
        self._event("Logout requested — owner liveness challenge", "logout_start",
                    subject=self.session_owner)

    def logout_cancel(self):
        if self.phase == "LOGOUT_CONFIRM":
            self.reset()

    def set_amount(self, value):
        if self.phase != "IDLE":
            return
        try:
            pence = int(round(float(value) * 100))
        except (TypeError, ValueError):
            return
        with self._lock:
            self.amount_pence = max(0, pence)
            self.amount_str = f"{self.amount_pence / 100:.2f}"

    def approve(self):
        if self.phase == "IDLE":
            self._start_payment()
        elif self.phase == "DUAL_PROMPT":
            with self._lock:
                self.phase = "DUAL_LIVENESS"
                self.challenge = Challenge()
                self.liveness = "ANALYSING"

    def submit_pin(self, value):
        self._submit_pin(str(value or ""))

    def register(self, name):
        if self.phase != "ENROLL_OFFER":
            return
        self._begin_enroll(name, as_owner=False)

    # ---- liveness / scan dispatch --------------------------------------

    def _liveness_done(self):
        passed = self.challenge.passed

        if self.phase == "DUAL_LIVENESS":
            if not passed:
                self._event("Approver liveness failed", "liveness_failure")
                self._finish(False, "APPROVAL FAILED", "approver liveness failed")
                return
            with self._lock:
                self.phase = "DUAL_SCANNING"
                self.prompt = "IDENTIFYING APPROVER…"
            self._start_scan(self._last_frame)
            return

        if not passed:
            with self._lock:
                self.liveness = "FAILED"
            if self.intent == "pay":
                rate_limit.record("failure")
                self._event("Liveness challenge failed", "liveness_failure")
                self._finish(False, "LIVENESS FAILED")
            elif self.intent == "owner_login":
                self._event("Owner liveness failed", "liveness_failure")
                self._finish(False, "LOGIN FAILED", "liveness failed")
            elif self.intent == "logout":
                self._event("Logout liveness failed", "liveness_failure")
                self._finish(False, "LOG OUT FAILED", "liveness failed")
            return

        self._event("Liveness challenge passed", "liveness_passed")
        with self._lock:
            self.liveness = "PASSED"
            self.phase = "SCANNING"
            self.prompt = "IDENTIFYING…"
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

        if self.phase == "DUAL_SCANNING":
            ok, why = approval.check(self.payer, name)
            self._event("Second approval granted" if ok else f"Second approval denied — {why}",
                        "dual_approval_granted" if ok else "dual_approval_denied",
                        subject=self.payer, detail=why)
            if ok:
                self._charge()
            else:
                self._finish(False, "APPROVAL DECLINED", why)
            return

        if self.intent == "owner_login":
            self._owner_login_scan(name, distance)
        elif self.intent == "logout":
            self._logout_scan(name, distance)
        else:
            self._pay_scan(name, distance)

    # ---- owner login ---------------------------------------------------

    def _owner_login_scan(self, name, distance):
        if name in ("UNKNOWN", "NO_FACE"):
            dist = None if distance == float("inf") else round(distance, 3)
            self._event("Owner not recognised", "auth_failure", detail=f"closest={dist}")
            with self._lock:
                self.identity = {"match": "NOT RECOGNISED", "confidence": None,
                                 "user_id": None, "distance": dist}
                self.phase = "OWNER_ENROLL_OFFER"
                self.prompt = "Not recognised — register as owner?"
            return
        rate_limit.record("success")
        self._event(f"Owner logged in: {name}", "owner_login_success", subject=name,
                    detail=f"distance={distance:.3f}")
        with self._lock:
            self.identity = {"match": "VERIFIED", "confidence": round((1 - distance) * 100, 1),
                             "user_id": name, "distance": round(distance, 3)}
        self._open_session(name)

    def _open_session(self, name):
        wallet_store.ensure_account(name, "customer", OPENING_BALANCE_PENCE)
        with self._lock:
            self.session_owner = name
            self.session_id = uuid.uuid4().hex[:11].upper()
        self._event(f"Session opened — owner {name}", "session_open", subject=name)
        self._finish(True, "SESSION STARTED", f"welcome {name}")

    # ---- logout --------------------------------------------------------

    def _logout_scan(self, name, distance):
        if name == self.session_owner:
            self._event("Session ended by owner", "session_close", subject=name,
                        detail=f"distance={distance:.3f}")
            with self._lock:
                self.phase = "RESULT"
                self.result = {"ok": True, "big": "SESSION ENDED", "small": f"goodbye {name}"}
            threading.Timer(CLOSE_SECONDS, self._close_app).start()
        else:
            self._event("Logout failed — not the session owner", "logout_failure")
            self._finish(False, "LOG OUT FAILED", "not the session owner")

    def _close_app(self):
        self._running = False
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass

    # ---- client payment ------------------------------------------------

    def _start_payment(self):
        if self.amount_pence <= 0:
            return
        locked, wait = rate_limit.locked_out()
        if locked:
            self._event(f"Locked out — retry in {wait}s", "lockout", detail=f"{wait}s")
            self._finish(False, "TEMPORARILY LOCKED", f"try again in {wait}s")
            return
        self._event("Payment session initialised", "session_start", detail=f"amount={self.amount_pence}")
        self._event("Liveness challenge initiated", "liveness_start")
        with self._lock:
            self.intent = "pay"
            self.phase = "LIVENESS"
            self.challenge = Challenge()
            self.liveness = "ANALYSING"

    def _pay_scan(self, name, distance):
        if name in ("UNKNOWN", "NO_FACE"):
            rate_limit.record("failure")
            dist = None if distance == float("inf") else round(distance, 3)
            self._event("Identity not recognised", "auth_failure", detail=f"closest={dist}")
            with self._lock:
                self.identity = {"match": "NOT RECOGNISED", "confidence": None,
                                 "user_id": None, "distance": dist}
                self.phase = "ENROLL_OFFER"
                self.prompt = "Not recognised — register a new user?"
            self._event("Offering enrolment to unrecognised face")
            return
        rate_limit.record("success")
        self._event(f"Identity matched: {name}", "auth_success", subject=name,
                    detail=f"distance={distance:.3f}")
        with self._lock:
            self.payer = name
            self.identity = {"match": "VERIFIED", "confidence": round((1 - distance) * 100, 1),
                             "user_id": name, "distance": round(distance, 3)}
        decision, reason = risk.assess(self.amount_pence, rate_limit.failure_count())
        with self._lock:
            self.decision, self.reason = decision, reason
        if decision == "allow":
            self._event("Risk engine: low risk — no step-up")
            self._charge()
        else:
            self._event(f"Risk engine: step-up required ({reason})", "stepup_required",
                        subject=name, detail=reason)
            self._enter_pin()

    def _enter_pin(self):
        with self._lock:
            self.phase = "PIN"
            self.pin_mode = "verify" if pin_auth.has_pin(self.payer) else "set"
            self.prompt = "Enter your PIN" if self.pin_mode == "verify" else "Set a new PIN"

    def _submit_pin(self, entry):
        if self.phase != "PIN" or not entry:
            return
        if self.pin_mode == "verify":
            if pin_auth.verify_pin(self.payer, entry):
                self._after_pin()
            else:
                self._event("Incorrect PIN")
                with self._lock:
                    self.prompt = "Incorrect PIN — try again"
        elif self.pin_mode == "set":
            self._pin_first = entry
            with self._lock:
                self.pin_mode = "confirm"
                self.prompt = "Confirm your PIN"
        else:
            if entry == self._pin_first:
                pin_auth.set_pin(self.payer, entry)
                self._after_pin()
            else:
                with self._lock:
                    self.pin_mode = "set"
                    self.prompt = "PINs did not match — set again"

    def _after_pin(self):
        self._event("PIN verified", "stepup_success", subject=self.payer)
        with self._lock:
            self.pin_mode = None
        if self.decision == "dual_approval":
            self._event("Second approver required")
            with self._lock:
                self.phase = "DUAL_PROMPT"
                self.prompt = "Second approval needed — approver, look and confirm"
        else:
            self._charge()

    def _charge(self):
        owner = self.session_owner
        wallet_store.ensure_account(self.payer, "customer", OPENING_BALANCE_PENCE)
        wallet_store.ensure_account(owner, "customer", OPENING_BALANCE_PENCE)
        try:
            wallet_store.transfer(uuid.uuid4().hex, self.payer, owner, self.amount_pence)
        except wallet_store.InsufficientFunds:
            balance = wallet_store.get_balance(self.payer)
            self._event("Payment declined — insufficient funds", "payment_declined", subject=self.payer)
            self._finish(False, "DECLINED — LOW FUNDS", f"balance {money(balance)}")
            return
        self._event(f"Payment authorised {money(self.amount_pence)} — {self.payer} → {owner}",
                    "payment", subject=self.payer, detail=f"{money(self.amount_pence)} to {owner}")
        self._finish(True, "PAYMENT AUTHORISED", f"{money(self.amount_pence)} · {self.payer} → {owner}")

    # ---- enrolment (client or owner) -----------------------------------

    def _begin_enroll(self, name, as_owner):
        name = (name or "").strip().replace(" ", "_")
        if not name:
            return
        with self._lock:
            self.enroll_name = name
            self.enroll_progress = 0
            self.phase = "OWNER_ENROLLING" if as_owner else "ENROLLING"
            self.prompt = f"Enrolling {name}…"
        self._event(f"Enrolment started for {name}", "enrol_start", subject=name)
        threading.Thread(target=self._enroll, args=(name, as_owner), daemon=True).start()

    def _enroll(self, name, as_owner):
        embeddings = []
        attempts = 0
        while len(embeddings) < ENROL_FRAMES and attempts < MAX_ENROL_ATTEMPTS:
            attempts += 1
            frame = self._last_frame
            if frame is None:
                time.sleep(0.1)
                continue
            cv2.imwrite(str(SCAN_PATH), frame)
            try:
                emb = create_embedding(SCAN_PATH)
            except ValueError:
                time.sleep(ENROL_FRAME_GAP)
                continue
            finally:
                SCAN_PATH.unlink(missing_ok=True)  # no raw image retained
            embeddings.append(emb)
            with self._lock:
                self.enroll_progress = len(embeddings)
            time.sleep(ENROL_FRAME_GAP)

        if not embeddings:
            self._event("Enrolment failed — no clear face captured", "enrol_failure")
            self._finish(False, "ENROLMENT FAILED", "no clear face captured")
            return

        template_store.enrol_identity(name, embeddings)
        self._event(f"Enrolled {name} — {len(embeddings)} encrypted templates",
                    "enrol", subject=name, detail=f"{len(embeddings)} templates")
        if as_owner:
            self._open_session(name)
        else:
            self._finish(True, "REGISTERED", f"{name} enrolled — you can now pay")

    # ---- result --------------------------------------------------------

    def _finish(self, ok, big, small=""):
        with self._lock:
            self.phase = "RESULT"
            self.result = {"ok": ok, "big": big, "small": small}
        threading.Timer(RESULT_SECONDS, self.reset).start()


# ---- Flask app ---------------------------------------------------------

app = Flask(__name__, static_folder=str(FRONTEND_DIST), static_url_path="")
engine = None  # created in main()


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/api/state")
def api_state():
    return jsonify(engine.to_state())


@app.route("/api/command", methods=["POST"])
def api_command():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    value = data.get("value")
    dispatch = {
        "owner_login": lambda: engine.owner_login(),
        "owner_register": lambda: engine.owner_register(value),
        "logout": lambda: engine.request_logout(),
        "logout_confirm": lambda: engine.logout_confirm(),
        "logout_cancel": lambda: engine.logout_cancel(),
        "set_amount": lambda: engine.set_amount(value),
        "approve": lambda: engine.approve(),
        "submit_pin": lambda: engine.submit_pin(value),
        "register": lambda: engine.register(value),
        "cancel_enroll": lambda: engine.reset(),
        "reset": lambda: engine.reset(),
    }
    handler = dispatch.get(action)
    if handler:
        handler()
    return jsonify({"ok": True})


@app.route("/api/video")
def api_video():
    def stream():
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
        while True:
            frame = engine.frame_bytes()
            yield boundary + frame + b"\r\n"
            time.sleep(0.033)

    return Response(stream(), mimetype="multipart/x-mixed-replace; boundary=frame")


def _run_flask():
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)


def main():
    global engine
    if not FRONTEND_DIST.exists():
        raise SystemExit("frontend not built — run:  cd frontend && npm install && npm run build")
    engine = Engine()
    threading.Thread(target=_run_flask, daemon=True).start()
    time.sleep(0.6)  # let Flask bind

    import webview
    # Frameless: no OS title bar / border — the design floats as a large widget.
    # easy_drag lets you move it by dragging the background. Owner logout closes it.
    window = webview.create_window("FacePay", f"http://127.0.0.1:{PORT}",
                                   width=1000, height=600, frameless=True, easy_drag=True,
                                   resizable=False, zoomable=False)
    engine.window = window
    webview.start()

    engine._running = False
    engine.camera.release()
    engine.landmarker.close()


if __name__ == "__main__":
    main()
