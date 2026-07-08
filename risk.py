"""Risk-based (adaptive) authentication (Tier 6).

Chooses how much authentication a payment needs from its risk signals — the
amount and the recent failure history. Low-risk payments clear on the face alone
(an inherence factor); higher-risk payments require a PIN step-up (a knowledge
factor) on top, i.e. two-factor. This mirrors PSD2 Strong Customer
Authentication, which exempts low-value payments from strong auth.

The hard lockout for many repeated failures lives in rate_limit; this module
covers the middle ground — elevate to step-up — plus high-value payments.
"""

STEP_UP_THRESHOLD_PENCE = 5000   # payments at/above this always need a PIN step-up
SUSPICIOUS_FAILURES = 2          # this many recent failures elevates even small payments


def assess(amount_pence, recent_failures=0):
    """Return (decision, reason) where decision is 'allow' or 'step_up'."""
    if amount_pence >= STEP_UP_THRESHOLD_PENCE:
        return "step_up", "high-value payment"
    if recent_failures >= SUSPICIOUS_FAILURES:
        return "step_up", "recent failed attempts"
    return "allow", "low risk"
