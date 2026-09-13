"""Pure winner selection; no network or filesystem access."""
from collections import Counter

VIDEO_TYPES = {"video"}
VOICE_TYPES = {"voice"}

def tally(rows, metric):
    """Count contributions per user for a metric. Returns a Counter."""
    counts = Counter()
    if metric == "video":
        for row in rows:
            if row.get("type") in VIDEO_TYPES:
                counts[row["user_id"]] += 1
    elif metric == "voice":
        for row in rows:
            if row.get("type") in VOICE_TYPES:
                counts[row["user_id"]] += 1
    elif metric == "social":
        for row in rows:
            if row.get("is_reply") == "1":
                counts[row["user_id"]] += 1
        if not counts:  # nobody replied — fall back to most messages overall
            for row in rows:
                counts[row["user_id"]] += 1
    else:
        raise ValueError(f"unknown metric '{metric}' in awards.csv")
    return counts


def pick_winner(counts, exclude=()):
    """Return (user_id, count) for the top contributor, or None.

    Users in `exclude` are skipped, so someone who already won another award
    can't win this one too (one person = one badge). Ties are broken by
    user_id so the result is deterministic.
    """
    eligible = {uid: c for uid, c in counts.items() if uid not in exclude}
    if not eligible:
        return None
    best = max(eligible.values())
    if best <= 0:
        return None
    winners = sorted(uid for uid, c in eligible.items() if c == best)
    return winners[0], best


