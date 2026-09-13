"""Strike transitions; callers own all storage and Telegram actions."""

def _int(v, default=0):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def apply_week_result(member, passed, week_iso, cfg):
    """Advance one member's strike state for a just-finished week.

    Mutates `member` in place and returns an action string:
      pass | strike1 | streak | cleared | remove | strike2_flagged | frozen

    The caller is responsible for the side effects each action implies
    (sending the DM, kicking, writing removals.csv, setting status=removed).
    """
    clear_weeks = _int(cfg.get("clear_weeks", 4), 4)
    strikes = _int(member.get("strikes"), 0)
    streak = _int(member.get("success_streak"), 0)

    if strikes == 0:
        member["success_streak"] = "0"
        if passed:
            return "pass"
        member["strikes"] = "1"
        member["strike1_week"] = week_iso
        return "strike1"

    if strikes == 1:
        if passed:
            streak += 1
            member["success_streak"] = str(streak)
            if streak >= clear_weeks:
                member["strikes"] = "0"
                member["strike1_week"] = ""
                member["success_streak"] = "0"
                return "cleared"
            return "streak"
        # failed a week while Strike 1 is still active
        member["success_streak"] = "0"
        member["strikes"] = "2"
        return "remove" if cfg.get("auto_removal") else "strike2_flagged"

    # strikes >= 2 already
    if cfg.get("auto_removal") and member.get("status") != "removed":
        return "remove"
    return "frozen"


