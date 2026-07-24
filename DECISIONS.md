# Design Decisions & Interview Prep

A running log of the non-obvious decisions, trade-offs, and "gotcha" moments in
this project — captured so they can be explained confidently in interviews.
Each entry: the question, what we chose, *why*, the trade-off, and a likely
interview angle.

---

## Phase 0 — Framing

### D1 — Enrolment, not a "search the citizen database"
- **Question:** How does the system know who a face belongs to — does it search a national/citizen database?
- **Decision:** No global database. Every user **enrols** first (their face is captured and stored), then recognition matches against enrolled users only.
- **Why:** No such searchable population database is accessible to a developer (or to real companies) — it's legally and practically off-limits. Every real biometric system works this way: Face ID makes you register your face; Amazon One registers your palm; e-gates read the passport chip. Enrolment *is* the architecture, not a shortcut.
- **Trade-off:** The database starts empty and only knows people who signed up. That's correct behaviour, not a limitation.
- **Interview angle:** *"How does a face map to an identity with no central database?"* → Enrolment-based 1:N matching; there is no magic lookup.

### D2 — Local model (DeepFace) over a cloud API (AWS Rekognition / Azure Face)
- **Decision:** Run recognition locally with DeepFace instead of calling a hosted face API.
- **Why:** (1) APIs still require enrolment — they don't solve the "no database" problem, they just host your enrolled faces. (2) The project's deep pillars — secure template handling, liveness, and the latency study — all require access to the raw embedding, which an API hides behind a black box. (3) Free, offline, no rate limits.
- **Trade-off:** More setup and slower on CPU than a managed service; we own the ops.
- **Interview angle:** *"Why not just use a cloud face API?"* → It would make the interesting engineering (template security, latency trade-offs) impossible to demonstrate.

---

## Phase 1 — Baseline recognition

### D3 — Faces as embeddings + distance, not a trained classifier
- **Decision:** Use a pre-trained network (FaceNet512) to turn each face into a 512-number vector; compare vectors by distance. No model training.
- **Why:** Same person → nearby vectors; different people → far apart. Recognition becomes a distance comparison. Training a face model from scratch is out of scope and unnecessary.
- **Interview angle:** *"What is a face embedding?"* → A learned vector where geometric closeness ≈ same identity.

### D4 — Cosine distance + a decision threshold
- **Decision:** Match by cosine distance; accept if below a threshold, else reject as `UNKNOWN`.
- **Why:** The threshold is the **security ↔ usability dial**. Lower = stricter (fewer strangers accepted, but genuine users rejected more). Higher = looser (all genuine users pass, but strangers slip in).
- **Interview angle:** *"How did you choose the threshold?"* → From measured FAR/FRR (see D9), erring stricter because this is a payment system.

### D5 — The 0.369 false rejection (a real observed failure)
- **Scenario:** A live webcam scan of the genuine enrolled user returned distance 0.369 — just over the 0.30 threshold — so the real user was **wrongly rejected**.
- **Why it happened:** Enrolment photos are posed and well-lit; a live webcam frame differs in lighting, angle, and image quality, so even the same person lands further away. Real-world capture is messier than reference images.
- **Fixes:** Tune the threshold from data; enrol from multiple live frames (planned) so references match real conditions.
- **Interview angle:** *"Where did your system fail and why?"* → This exact story: enrolment/inference distribution mismatch.

### D6 — 1:N identification, not 1:1 verification (why this is "pay-by-face", not "Face ID login")
- **Decision:** `identify_face` searches the **entire** enrolled set to determine *who* the person is.
- **Why:** Face ID does 1:1 verification — the device already knows whose it is and only asks "is it the owner?". A payment terminal has no idea who walked up and must **identify** them among all enrolled users (1:N), like Amazon One. The face then resolves to *that person's wallet* — the face is the payment credential. 1:N is the harder problem.
- **Interview angle:** *"Isn't this just face login?"* → No — 1:N identification tied to a financial account, not 1:1 device unlock.

### D7 — RetinaFace → OpenCV face detector
- **Decision:** Switched the detector backend from `retinaface` to `opencv`.
- **Why:** RetinaFace is more accurate but heavy on CPU and downloads a model on first use (looked like a hang). OpenCV's detector ships built-in and is fast — fine for a clean webcam scan.
- **Trade-off:** Lower detection accuracy on hard images for much lower latency and no download. This is a concrete **latency vs accuracy** decision to revisit in the Tier 7 architecture study.
- **Interview angle:** *"What latency trade-offs did you make?"* → Detector choice is one; on-device vs cloud is the bigger one later.

### D8 — Resilient enrolment (skip undetectable photos)
- **Decision:** If no single clean face is detected in an enrolment image, log and skip it instead of crashing.
- **Why:** Real datasets (LFW) are messy; one bad image shouldn't abort enrolling hundreds.
- **Interview angle:** Robustness to dirty input data.

### D9 — LFW dataset for evaluation
- **Decision:** Use Labeled Faces in the Wild to populate a realistic enrolled base **and** a set of never-enrolled "strangers".
- **Why:** With a database of one, you can't measure the security half (false accepts). You need many non-user faces to test that strangers are rejected. LFW is the canonical benchmark.
- **Trade-off / honest caveat:** LFW skews toward well-lit photos of public figures — *easier* than real payment conditions. That's partly why the live webcam gave the harder 0.369 result.
- **Interview angle:** *"What are the limits of your evaluation?"* → Naming this dataset-vs-reality gap shows you understand your numbers.

### D10 — Threshold-sweep evaluation (FAR / FRR / misident) → chose 0.25
- **Decision:** `evaluate.py` sweeps thresholds and reports false-reject, false-accept, and misidentification per threshold. **Chosen operating point: 0.25.**
- **Result (30 genuine + 30 stranger LFW images):**

  | Threshold | False reject | False accept | Misident |
  |-----------|-------------:|-------------:|---------:|
  | 0.20 | 47% | 0% | 0 |
  | **0.25** | **20%** | **0%** | **0** |
  | 0.30 | 17% | 3% | 0 |
  | 0.35 | 7% | 10% | 1 |
  | 0.40 | 3% | 10% | 1 |
  | 0.45 | 0% | 20% | 6 |

- **Why 0.25:** The *loosest* threshold that still has **0% false accepts and 0 misidentification** — the most usable point at which no stranger is ever authorised and no one is charged to the wrong account. Loosening to 0.30 saves one false reject but introduces a false accept — a bad trade when money moves. Stricter (0.20) triples false rejects for no security gain. For payments, false accepts/misident are the costly errors; the 20% false-reject is a usability cost mitigated by re-scan + PIN fallback (Tier 6).
- **Key finding — the LFW ↔ reality gap:** The live webcam scan of the genuine user measured **0.369**, *above* the chosen 0.25 — so the real user would be rejected. This is not a threshold bug; it's an **enrolment** problem (posed enrolment photos vs a live webcam frame — see D5). Fix: enrol from live webcam frames (multi-frame enrolment) to pull genuine live distances down, *without* loosening the threshold and admitting strangers.
- **Interview angle:** *"How did you pick the threshold, and what did evaluation reveal?"* → Chose the most usable 0%-false-accept point; evaluation then surfaced that **enrolment quality, not the threshold**, is the real constraint on live usability.

---

## Cross-cutting

### D11 — Biometric data is git-ignored (privacy by default)
- **Decision:** `known/`, `test/`, and `scan.jpg` are never committed; the repo holds code only, and the dataset is regenerated via `build_dataset.py`.
- **Why:** Committing face photos to a public repo publishes personal biometric data — the wrong move for a payment-security project. Repos hold code, not data.
- **Interview angle:** Privacy-conscious engineering; a small but deliberate security decision.

---

## Phase 2 — Liveness

### D12 — Challenge–response liveness
- **Decision:** Defeat spoofs with a **random** challenge (blink / head-turn) the user must perform on demand.
- **Why:** A printed photo can't blink; a static image can't turn; a replayed video can't blink *exactly when randomly asked*. Randomness is what beats replay attacks.
- **Implemented as:** a random target of 2–4 blinks within a 6-second window; PASS on reaching the target, FAIL on timeout. The per-attempt random count is the anti-replay mechanism.
- **Interview angle:** *"Why did replay attacks perform worse than photo attacks?"* → because a replay can contain a blink, so it stresses the on-demand/timing aspect specifically.

### D13 — MediaPipe for landmarks (blink via Eye Aspect Ratio) over dlib
- **Decision:** Use MediaPipe Face Mesh landmarks + Eye Aspect Ratio for blink detection.
- **Why:** dlib's landmark predictor is painful to install on Windows; MediaPipe is real-time on CPU and installs cleanly. EAR (eye height ÷ width) drops sharply on a blink — a simple, explainable signal, no training needed.
- **Implementation gotcha:** mediapipe 0.10.35 **removed the legacy `mp.solutions.face_mesh` API** — only the newer Tasks `FaceLandmarker` is available, which needs a `face_landmarker.task` model file (auto-downloaded on first run, git-ignored). Adapted to the Tasks API rather than downgrading mediapipe, because an older mediapipe would force `numpy < 2` and clash with TensorFlow's `numpy 2.x`.
- **Interview angle:** *"How does blink detection work without ML training?"* → Geometric landmark ratio over time. Also a dependency-conflict story: resolved a removed-API break without breaking the numpy/TensorFlow version constraints.

### D14 — Blink reliability is bounded by frame rate, not the threshold
- **Scenario:** With a healthy resting EAR (~0.38) and blinks clearly dropping below 0.21, blinks were still *intermittently missed*.
- **Cause:** On CPU, per-frame `IMAGE`-mode inference was slow enough that a ~100–150 ms blink sometimes fell entirely *between* two processed frames. Requiring 2 consecutive closed frames compounded it, dropping fast blinks.
- **Fix:** Switched to MediaPipe `VIDEO` running mode (tracks between frames → higher effective FPS) and lowered the closed-frame requirement to 1. Added a rolling-minimum EAR readout so closures are visible for calibration.
- **Interview angle:** *"What limited your liveness detection?"* → Temporal sampling: reliability depends on frame rate relative to blink duration, not the EAR threshold. Directly connects Tier 2 (liveness) to the Tier 7 latency study.

### D15 — Head-turn as a second challenge type (2D yaw proxy)
- **Decision:** Add a "turn your head LEFT/RIGHT" challenge alongside blink. The challenge *type* and *direction* are picked at random each attempt.
- **How:** Yaw is estimated from a 2D geometry proxy — nose-tip x-position relative to the midpoint between the outer eye corners, normalised by inter-eye distance — not a trained model.
- **Why two types:** Random type + random parameter (blink count / turn direction) widens the space an attacker must cover — they can't pre-stage a single spoof clip that satisfies whatever is asked.
- **Honest limitation:** this is a *2D* proxy for a *3D* head rotation. A flat photo physically rotated could partially fool it; genuinely robust liveness would use 3D head-pose (e.g. `solvePnP`) or a depth camera. Logged as a known gap, not hidden.
- **Calibration:** the yaw threshold is tuned against an on-screen live `yaw:` readout, the same approach used for the EAR threshold.
- **Interview angle:** *"How robust is your liveness, really?"* → Name the 2D-vs-3D limitation and the upgrade path (3D pose / depth). Showing you know where it breaks is stronger than claiming it's bulletproof.
