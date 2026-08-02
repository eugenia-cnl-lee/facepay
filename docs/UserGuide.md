# FacePay — User Guide

FacePay is a **pay-by-face terminal**. It's modelled as a store device: an **owner** signs in to
open a session, **clients pay with their face**, and every payment is credited to the signed-in
owner. This guide covers installing it, running it, and using every feature.

For the *why* behind the design see [DECISIONS.md](DECISIONS.md); for the security analysis see
[THREAT-MODEL.md](THREAT-MODEL.md).

---

## 1. Requirements

- **Python 3.11**, **Node.js 18+**, and a **webcam**.
- First run downloads face models (~90 MB) and, for evaluation, the LFW dataset.

See the [README](../README.md#prerequisites) for the full prerequisites table.

## 2. Install & build

```bash
# from the repository root
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -r backend/requirements.txt

# build the frontend once (needed before the first run)
cd frontend && npm install && npm run build && cd ..
```

## 3. Run the app

```bash
python backend/web_app.py
```

A frameless desktop window opens. On the very first run the encrypted store is empty, so the first
person to sign in enrols live.

> **Tip:** run it from the **repository root** (as above) so it finds your saved data
> (`faces.db`, `wallet.db`, the face model). Press **ESC** in the app to end a session; close the
> terminal with `Ctrl + C` if needed.

---

## 4. Using the app — the session lifecycle

### 4.1 Owner sign-in (boot)

When the app opens it's an **owner terminal**.

1. Click **LOG IN AS OWNER**.
2. Complete the **liveness challenge** — the prompt asks you to either **blink** a number of times
   or **turn your head** left/right. Do it before the countdown ends. Green brackets track your face.
3. Your face is matched against the enrolled identities.
   - **Recognised** → your session starts; the header shows `Owner: <name>` and a live **TAKINGS** balance.
   - **Not recognised** → you're offered *"Register as the owner?"* — type a name and click
     **REGISTER OWNER**. It captures 5 frames and starts your session.

### 4.2 Taking a payment (client)

Once a session is open, the screen is the **client-facing terminal**:

1. Type the **amount** and click **AUTHORISE WITH FACE**.
2. The client completes a **liveness challenge**, then their face is identified.
3. Depending on the amount, an extra step may appear (see the risk ladder below).
4. On success the screen shows **PAYMENT AUTHORISED**, and the owner's **TAKINGS** balance goes up.

### 4.3 Step-up authentication (higher amounts)

FacePay asks for more proof as the amount rises:

| Amount | What's required |
| --- | --- |
| Under £50 | Face + liveness only |
| £50 and above | Face + liveness **+ a PIN** (set on first use, entered thereafter) |
| £200 and above | The above **+ a second approver's** face & liveness (dual control) |

- **PIN:** if the payer has no PIN yet, they set one (enter, then confirm). Next time they just enter it.
- **Dual control:** a **second, different enrolled person** must approve by face. A payer cannot
  approve their own high-value payment.

### 4.4 Registering a new client

If a paying client isn't recognised, the terminal offers *"Register a new user?"* — type a name and
click **REGISTER**. It captures 5 encrypted face templates (no raw image is kept) and returns to the
terminal so they can pay.

> Accounts are **shared**: a face enrolled on the owner side is recognised on the client side and
> vice-versa — it's one identity store.

### 4.5 Ending the session

Press **ESC**. The screen asks *"End this session, `<owner>`?"*

- Click **YES, END** → the owner re-confirms with a **liveness + face** check. On success it shows
  **SESSION ENDED** and the app closes.
- A failed check (wrong face or failed liveness) shows **LOG OUT FAILED** and returns to the session.
- Click **NO** to stay in the session.

---

## 5. The dashboard, panel by panel

The right side of the screen is live telemetry — all of it driven by the real engine:

| Panel | What it shows |
| --- | --- |
| **Identity Verification** | Match status, confidence (cosine similarity), and the matched user's ID |
| **Presentation Attack Det.** | Liveness result (`AWAITING` / `ANALYSING` / `PASSED` / `FAILED`) and method |
| **Session Security** | Template encryption at rest (AES) and PIN hashing (PBKDF2) |
| **Risk Engine** | The step-up decision (`ALLOW` / `STEP-UP` / `DUAL CTRL`) and recent-failure count |
| **Audit Trail** | A live, timestamped log of every security event in the session |

The left panel is the **camera** with the liveness brackets; below it is the **interaction area**
(amount entry, PIN pad, prompts).

---

## 6. Companion command-line tools

These run from the repository root and are useful for demos and grading.

**Check recognition accuracy** (downloads LFW on first run):
```bash
python backend/build_dataset.py     # builds the evaluation gallery + test sets
python backend/evaluate.py          # prints the false-accept / false-reject / misidentification table
```

**Try liveness on its own:**
```bash
python backend/liveness.py          # a random blink/head-turn challenge with a face overlay
```

**Wallet — balance, history, refund:**
```bash
python backend/wallet.py balance <name>
python backend/wallet.py history <name>
python backend/wallet.py refund <payment_id>
```

**Inspect the audit trail:**
```bash
python -c "import sys; sys.path.insert(0, 'backend'); import audit; [print(r) for r in audit.recent()]"
```

---

## 7. Troubleshooting

| Symptom | Fix |
| --- | --- |
| **"CAMERA UNAVAILABLE"** | Close other apps using the webcam; check OS camera permissions, then relaunch. |
| **Liveness keeps failing** | Face the camera in good light; blink deliberately or turn your head clearly before the timer ends. |
| **"NOT RECOGNISED" for an enrolled person** | Re-enrol with the on-screen register flow — live multi-frame enrolment matches far better than a single posed photo. |
| **"frontend not built"** | Run `cd frontend && npm install && npm run build` before launching. |
| **Repeated failures lock the terminal** | This is the anti-brute-force lockout; wait for the countdown shown on screen. |

---

## 8. Privacy note

Biometric templates are **encrypted at rest** and the key is stored outside the database; **no raw
enrolment images are kept** (frames are embedded, then deleted). Your `faces.db`, `facepay.key`, and
`wallet.db` never leave your machine and are never committed to version control.
