"""Risk-based (adaptive) authentication (Tier 6).

Chooses how much authentication a payment needs from its risk signals — the
amount and the recent failure history — as a three-rung ladder:

    low            -> face only              (inherence)
    high-value     -> face + PIN             (+ knowledge)
    very high-value-> face + PIN + 2nd face  (+ a second person: dual control)

Higher-value payments require step-up; the very-high tier additionally requires
a second, different enrolled person to approve (two-person rule / maker-checker).
This mirrors PSD2 Strong Customer Authentication.

Thresholds are FIXED POLICY, not user-editable — a control the account holder
could raise is one an attacker who holds the account could disable (see D25).
The hard lockout for many repeated failures lives in rate_limit.
"""

STEP_UP_THRESHOLD_PENCE = 5000          # face + PIN at/above this (£50)
DUAL_APPROVAL_THRESHOLD_PENCE = 20000   # face + PIN + second approver at/above this (£200)
SUSPICIOUS_FAILURES = 2                 # this many recent failures elevates even small payments


def assess(amount_pence, recent_failures=0):
    """Return (decision, reason): decision is 'allow', 'pin_stepup', or 'dual_approval'."""
    if amount_pence >= DUAL_APPROVAL_THRESHOLD_PENCE:
        return "dual_approval", "very high-value payment"
    if amount_pence >= STEP_UP_THRESHOLD_PENCE:
        return "pin_stepup", "high-value payment"
    if recent_failures >= SUSPICIOUS_FAILURES:
        return "pin_stepup", "recent failed attempts"
    return "allow", "low risk"
