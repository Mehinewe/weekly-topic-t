"""Choose reminder recipients without sending or writing state."""


def select_targets(members, counts, already, week_monday, which, req, threshold,
                   force, is_trackable):
    week_iso = week_monday.isoformat()
    targets = []
    for uid, m in members.items():
        if not is_trackable(m, week_monday):
            continue
        got = counts.get(uid, 0)
        if got > threshold or got >= req:
            continue
        if not force and (week_iso, str(uid), which) in already:
            continue
        targets.append((uid, got))

    return targets
