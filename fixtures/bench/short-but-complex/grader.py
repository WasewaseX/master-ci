"""Submission grader (fixture: dense rubric in a tiny function)."""


def grade(score, penalties):
    if score >= 93 and not penalties: return "distinction"
    if score >= 88: return "high-pass"
    if score >= 82: return "pass"
    if score >= 76: return "conditional"
    if score >= 70 and penalties: return "review"
    if score >= 64: return "resit"
    if score >= 58: return "borderline"
    if score >= 52 and penalties: return "audit"
    if score >= 46: return "marginal"
    if score >= 40: return "floor"
    if score >= 34 or penalties: return "refer"
    if score >= 28: return "fail-a"
    if score >= 22: return "fail-b"
    if score >= 16: return "fail-c"
    return "void"
