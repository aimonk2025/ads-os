"""
Platform auto-detector.

Peeks at the first ~1KB of a CSV to identify which platform it came from,
without fully loading and cleaning the file.

Returns:
    {
        "platform": "google" | "meta" | "ga4" | "funnel" | "unknown",
        "confidence": 0-100,
        "matched": ["list", "of", "matched", "signals"],
        "hint": "Human-readable explanation"
    }
"""

import re
import io
import chardet
from pathlib import Path


# Signal sets per platform
# Each signal is (column_substring, weight)
# We read the header row plus the first comment lines for GA4

GOOGLE_SIGNALS = [
    ("conversion value",    30),
    ("quality score",       25),
    ("search impression",   25),
    ("cost / conv",         20),
    ("all conv",            15),
    ("impression share",    20),
    ("search lost",         15),
    ("match type",          15),
    ("keyword",             10),
    ("ad group",            10),
    ("campaign",             5),
    ("cost",                 5),
    ("conversions",          5),
    ("clicks",               3),
    ("impressions",          3),
]

META_SIGNALS = [
    ("purchase roas",       35),
    ("return on ad spend",  30),
    ("amount spent",        25),
    ("ad set name",         25),
    ("ad set id",           20),
    ("objective",           20),
    ("results",             15),
    ("reach",               10),
    ("frequency",           15),
    ("delivery",            10),
    ("campaign name",        8),
    ("clicks (all)",        15),
    ("link clicks",         10),
    ("impressions",          3),
]

GA4_SIGNALS = [
    ("google analytics",    50),   # comment line at top
    ("session campaign",    40),
    ("session source",      35),
    ("session default channel",  35),
    ("engaged sessions",    30),
    ("engagement rate",     25),
    ("bounce rate",         20),
    ("sessions",            15),
    ("avg. session",        20),
    ("key events",          25),
    ("total revenue",       10),
    ("new users",           10),
]

FUNNEL_SIGNALS = [
    ("mqls",                40),
    ("sqls",                40),
    ("marketing qualified", 40),
    ("sales qualified",     40),
    ("sql",                 30),
    ("mql",                 30),
    ("cost per lead",       25),
    ("leads",               20),
    ("opportunities",       20),
    ("customers",           15),
    ("close rate",          20),
    ("closed won",          25),
]


def _peek_csv(filepath: str, bytes_limit: int = 2048) -> str:
    """Read the first N bytes of a file, return as lowercase string."""
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    with open(filepath, "rb") as f:
        raw = f.read(bytes_limit)

    # Detect encoding from the sample
    enc = chardet.detect(raw).get("encoding") or "utf-8"
    try:
        text = raw.decode(enc, errors="replace")
    except Exception:
        text = raw.decode("latin-1", errors="replace")

    return text.lower()


def _score_signals(text: str, signals: list) -> tuple[int, list]:
    """Return (total_score, list_of_matched_signal_strings)."""
    score = 0
    matched = []
    for signal, weight in signals:
        if signal in text:
            score += weight
            matched.append(signal)
    return score, matched


def detect_platform(filepath: str) -> dict:
    """
    Auto-detect the platform of a CSV file.

    Returns dict with keys: platform, confidence, matched, hint
    """
    try:
        text = _peek_csv(filepath)
    except FileNotFoundError as e:
        return {"platform": "unknown", "confidence": 0, "matched": [], "hint": str(e)}
    except Exception as e:
        return {"platform": "unknown", "confidence": 0, "matched": [], "hint": f"Could not read file: {e}"}

    scores = {
        "google":  _score_signals(text, GOOGLE_SIGNALS),
        "meta":    _score_signals(text, META_SIGNALS),
        "ga4":     _score_signals(text, GA4_SIGNALS),
        "funnel":  _score_signals(text, FUNNEL_SIGNALS),
    }

    # Find the winner
    best_platform = max(scores, key=lambda p: scores[p][0])
    best_score, best_matched = scores[best_platform]
    second_score = sorted(s for p, (s, _) in scores.items() if p != best_platform)[-1] if len(scores) > 1 else 0

    # Confidence: based on margin over second place and absolute score
    if best_score == 0:
        confidence = 0
    else:
        margin = best_score - second_score
        # Scale: score>=50 + margin>=20 = high confidence
        raw_conf = min(100, int((best_score / 60) * 60 + (margin / 40) * 40))
        confidence = max(10, raw_conf)

    # Tie-breaking: if google and meta are very close, use exclusive signals
    g_score, _ = scores["google"]
    m_score, _ = scores["meta"]
    if abs(g_score - m_score) < 15:
        # Check exclusive signals
        has_purchase_roas = "purchase roas" in text or "return on ad spend" in text
        has_conv_value    = "conversion value" in text or "quality score" in text
        if has_purchase_roas and not has_conv_value:
            best_platform = "meta"
        elif has_conv_value and not has_purchase_roas:
            best_platform = "google"

    hints = {
        "google":  "Looks like Google Ads - detected Google-specific columns",
        "meta":    "Looks like Meta Ads - detected Meta-specific columns",
        "ga4":     "Looks like GA4 export - detected Google Analytics columns",
        "funnel":  "Looks like funnel/CRM data - detected lead/MQL/SQL columns",
        "unknown": "Could not identify platform from column headers",
    }

    platform = best_platform if confidence >= 20 else "unknown"

    return {
        "platform":   platform,
        "confidence": confidence,
        "matched":    best_matched[:6],   # top 6 signals for display
        "hint":       hints.get(platform, hints["unknown"]),
        "all_scores": {p: s for p, (s, _) in scores.items()},
    }


def detect_platform_from_bytes(file_bytes: bytes, filename: str = "") -> dict:
    """
    Auto-detect from raw bytes (for use in web upload handlers before saving to disk).
    """
    # Detect encoding
    enc = chardet.detect(file_bytes[:4096]).get("encoding") or "utf-8"
    try:
        text = file_bytes[:2048].decode(enc, errors="replace").lower()
    except Exception:
        text = file_bytes[:2048].decode("latin-1", errors="replace").lower()

    scores = {
        "google":  _score_signals(text, GOOGLE_SIGNALS),
        "meta":    _score_signals(text, META_SIGNALS),
        "ga4":     _score_signals(text, GA4_SIGNALS),
        "funnel":  _score_signals(text, FUNNEL_SIGNALS),
    }

    best_platform = max(scores, key=lambda p: scores[p][0])
    best_score, best_matched = scores[best_platform]
    second_score = sorted(s for p, (s, _) in scores.items() if p != best_platform)[-1] if len(scores) > 1 else 0

    if best_score == 0:
        confidence = 0
    else:
        margin = best_score - second_score
        raw_conf = min(100, int((best_score / 60) * 60 + (margin / 40) * 40))
        confidence = max(10, raw_conf)

    g_score, _ = scores["google"]
    m_score, _ = scores["meta"]
    if abs(g_score - m_score) < 15:
        has_purchase_roas = "purchase roas" in text or "return on ad spend" in text
        has_conv_value    = "conversion value" in text or "quality score" in text
        if has_purchase_roas and not has_conv_value:
            best_platform = "meta"
        elif has_conv_value and not has_purchase_roas:
            best_platform = "google"

    hints = {
        "google":  "Google Ads",
        "meta":    "Meta Ads",
        "ga4":     "GA4 / Google Analytics",
        "funnel":  "Funnel / CRM data",
        "unknown": "Unknown format",
    }

    platform = best_platform if confidence >= 20 else "unknown"

    return {
        "platform":   platform,
        "confidence": confidence,
        "matched":    best_matched[:6],
        "hint":       hints.get(platform, hints["unknown"]),
    }
