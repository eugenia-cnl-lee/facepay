# FacePay — Threat Model

Security analysis of the biometric authentication + payment flow. Each threat is
mapped to a mitigation with **honest status** (implemented / partial / planned /
out-of-scope). Face data is treated as **GDPR Article 9 "special category" data** —
the highest-protection tier.

> Status legend: ✅ implemented · 🟡 partial · ⬜ planned (Tier 3) · ⏭️ future / out of scope

---

## 1. Scope

**In scope:** enrolment, liveness, identification, and the authentication decision that
authorises a (simulated) payment.

**Out of scope (this version):** the payment network itself, network transport (single-device
app — transport security is the Tier 7 architecture study), OS/hardware compromise, and
physical coercion.

## 2. Assets (what we protect, ranked)

| ID | Asset | Why it matters |
|----|-------|----------------|
| A1 | **Biometric templates** (face embeddings) | Irreplaceable — you cannot reissue a face. Highest value. |
| A2 | Identity ↔ account linkage | Binds a face to money. |
| A3 | The accept/reject decision | The gate that authorises payment. |
| A4 | Enrolment integrity | Controls *who is allowed to become* an identity. |
| A5 | Wallet balance / transaction integrity | The money itself (Tier 4–5). |
| A6 | Audit trail | Detection, forensics, non-repudiation. |

## 3. Trust boundary

Everything from the camera **inward** is the trusted device. The **camera input itself is
untrusted** — an attacker fully controls what is presented to it (a photo, a screen, a mask),
and in a compromised setup could inject synthetic frames. Every threat below starts at that
boundary.

```
[UNTRUSTED: whatever is in front of the camera]
        │
        ▼
   capture → liveness → detect → embed → match → decision → payment
        └───────────────── trusted device ─────────────────┘
```

## 4. Attacker profiles

| Profile | Capability |
|---------|-----------|
| **Opportunist** | Has a photo of the victim (e.g. social media). No special tools. |
| **Motivated** | Can replay video on a phone, print masks, record the victim. |
| **Prober** | Can call the match path repeatedly to reverse-engineer an accepting input. |
| **Insider** | Has read access to the host / database. |

## 5. Threats & mitigations

STRIDE codes: **S**poofing · **T**ampering · **R**epudiation · **I**nfo disclosure ·
**D**enial of service · **E**levation of privilege.

| # | Threat / attack | STRIDE | Impact | Mitigation | Status |
|---|-----------------|--------|--------|-----------|--------|
| T1 | **Photo presentation** — hold a printed/photo of the victim | S | Impersonation | Liveness challenge (blink) — a photo can't blink | ✅ |
| T2 | **Replay / screen** — play a recorded video of the victim | S | Impersonation | **Random** challenge (type + count + direction) — a pre-recorded clip can't satisfy an unpredictable, on-demand prompt | 🟡 |
| T3 | **Stranger false-accept** — an unrelated person is accepted | S | Wrong person pays | Decision threshold tuned from FAR/FRR data (0.25 → 0% false-accept on test set) | ✅ |
| T4 | **Misidentification** — accepted as the *wrong* enrolled user | T | Wrong account charged | Threshold + **gallery hygiene** (only real principals enrolled; eval data kept separate) — see decision D18 | ✅ |
| T5 | **Template theft** — DB is stolen; embeddings reused or a face reconstructed | I | Permanent biometric compromise | **Encrypt templates at rest** (can't hash — approximate matching) + key management + **no raw images retained** + separate identity/biometric stores | ⬜ |
| T6 | **Hill-climbing / similarity probing** — repeatedly tweak an input, watch the score, climb toward acceptance | I / S | Forged accepting input | **Rate limiting** + lockout on the match path (persisted, so a restart can't reset it) | ✅ |
| T7 | **Enrolment poisoning** — attacker enrols *their* face under the victim's name | S / E | Account takeover at the root | Secure enrolment (identity proofing / step-up before enrol) | ⬜ |
| T8 | **Repudiation** — user denies a transaction they made | R | Dispute / fraud | **Append-only audit log** of enrolment, auth decisions, liveness failures, lockouts | ✅ |
| T9 | **Raw image exposure** — stored enrolment photos leak | I | Biometric + PII leak | **Store no raw image after enrolment** (keep only the encrypted vector) | ⬜ |
| T10 | **Template linkage** — same template correlates the user across systems | I | Cross-system tracking | Cancelable / transformed templates (revocable transform) | ⏭️ |
| T11 | **Frame injection / deepfake feed** — bypass the camera, feed synthetic frames straight to the pipeline | S | Full liveness bypass | Hardware attestation / secure camera path | ⏭️ |
| T12 | **Transport interception** — read/alter data in a client↔server split | I / T | Data theft / MITM | TLS in transit (only relevant once distributed) | ⏭️ Tier 7 |
| T13 | **Single-factor compromise** — a spoof or stolen biometric clears a high-value payment on the face alone | S / E | Large fraudulent charge | **Risk-based step-up** — PIN (knowledge factor) required on top of the face for high-value / suspicious payments | ✅ |

## 6. Residual risks (where this system honestly breaks)

- **Liveness is a 2-D proxy.** Blink/EAR and a nose-vs-eye yaw estimate can be partially defeated by a rotated photo or a high-quality replayed video (decision D15). A real fix needs **3-D / depth-based PAD** (ISO/IEC 30107).
- **No frame-injection defence.** An attacker who can feed frames directly to the pipeline (bypassing the physical camera) defeats liveness entirely — this needs hardware attestation.
- **Threshold generality.** 0.25 was tuned on LFW, which is *easier* than live capture; it should be re-evaluated with the deployed detector (D18).
- **Single device.** Transport and server-side threats are deferred to the Tier 7 architecture study.

## 7. Standards referenced

- **ISO/IEC 30107** — Presentation Attack Detection (the liveness pillar, Tier 2)
- **ISO/IEC 24745** — Biometric template protection (encryption / cancelable templates)
- **GDPR Article 9** (special-category data) & **Article 17** (right to erasure → template deletion)
- **NIST SP 800-63B** — biometric authenticator guidance (e.g. limit false-match attempts)
