# FacePay

A privacy-conscious biometric payment system built around face recognition.
The current milestone implements **baseline recognition**: enrolling users,
generating face embeddings, matching a live webcam scan against enrolled
identities, rejecting unknown faces, and evaluating accuracy across thresholds.

## Prerequisites

### System

| Requirement | Details |
| --- | --- |
| **Python** | 3.11 recommended (developed on 3.11.9). Avoid 3.12+ — TensorFlow support lags. |
| **Webcam** | Required for live scanning and enrolment (`recognition.py`). |
| **Internet** | Needed on first run — downloads the FaceNet model (~90 MB), the LFW dataset (~200 MB), and the MediaPipe face-landmark model (~4 MB). All cached afterwards. |
| **Disk space** | ~500 MB free for models and the dataset. |
| **OS** | Cross-platform; developed and tested on Windows 11. |

### Python packages

Installed via `requirements.txt`:

- [`deepface`](https://github.com/serengil/deepface) — face detection and FaceNet512 embeddings
- `tensorflow` + `tf-keras` — deep-learning backend used by deepface
- `opencv-python` — webcam capture and image I/O
- `numpy` — vector maths for similarity
- `scikit-learn` — downloads the LFW dataset for evaluation
- `mediapipe` — face-mesh landmarks for blink-based liveness detection

## Installation

```bash
# 1. Clone
git clone https://github.com/eugenia-cnl-lee/facepay.git
cd facepay

# 2. Create and activate a virtual environment (Windows PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt
```

> On macOS/Linux, activate the environment with `source .venv/bin/activate`.

## Usage

**The full app** — prove liveness, identify, enrol if new, then pay (simulated):

```bash
python facepay.py
```

Or run each component on its own:

```bash
# 1. Build the dataset — enrolled users + genuine/stranger test sets (first run downloads LFW)
python build_dataset.py

# 2. Live recognition — scan a face from the webcam and identify it
python recognition.py

# 3. Evaluate — accuracy across a range of decision thresholds
python evaluate.py

# 4. Liveness challenge — blink or turn your head to pass a random challenge
python liveness.py
```

## Project structure

| File | Purpose |
| --- | --- |
| `facepay.py` | End-to-end app: liveness gate → identify → live enrol → simulated payment |
| `recognition.py` | Enrolment, embeddings, similarity matching, webcam scan, unknown-face rejection |
| `build_dataset.py` | Builds `known/` and `test/` from the LFW dataset |
| `liveness.py` | Challenge-response liveness: blink + head-turn (MediaPipe landmarks) |
| `evaluate.py` | Reports false-accept / false-reject / misidentification rates per threshold |
| `logging_setup.py` | Silences TensorFlow startup logs (imported before deepface) |
| `requirements.txt` | Pinned Python dependencies |
| `THREAT-MODEL.md` | Security threat model — assets, attackers, STRIDE + biometric threats, mitigations |
| `DECISIONS.md` | Design decisions & interview-prep log |

Face data (`known/`, `test/`) and webcam captures (`scan.jpg`) are git-ignored:
they are personal biometric data and regenerable, so the repository holds code
only. Run `build_dataset.py` to reproduce the dataset locally.
