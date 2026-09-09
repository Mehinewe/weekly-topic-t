"""
Participation reminders — Wednesday / Friday nudges
==================================================

Encouraging, non-punitive. DMs members who are behind on this week's goal so
they still have time to catch up.

    python send_reminders.py --which wed --dry-run    # preview, sends nothing
    python send_reminders.py --which wed              # real send
    python send_reminders.py --which fri --force      # ignore the "already reminded" log

  wed : remind members with <= reminders.first.at_most_active_days  (default 0-1)
  fri : remind members with <= reminders.second.at_most_active_days (default 0-2)

Idempotency: each (week, member, which) is recorded in reminder_log.csv, which
the workflow commits back, so backup cron times don't double-remind. A member
with no private chat with the bot is recorded too (retrying won't help); other
send errors are left unrecorded so a later run retries.

Required env vars for a real send: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
"""

import os
import sys
import time

import participation as P

WHICH_TO_KEY = {"wed": "first", "fri": "second"}
DM_PAUSE_SECONDS = 1.1   # stay well under Telegram's ~30 msg/s ceiling


def main():
    P.load_dotenv()
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    force = "--force" in args

    which = None
    for i, a in enumerate(args):
        if a == "--which" and i + 1 < len(args):
            which = args[i + 1].lower()
    if which not in WHICH_TO_KEY:
        P._fail("pass --which wed|fri")

    cfg = P.load_config()
    messages = P.load_messages()
    if not cfg["enabled"].get("reminders", True):
        print("Reminders are disabled in participation_config.json — nothing to do.")
        return

    today = P.today_in_tz(cfg)
    starts = P.parse_date(cfg.get("tracking_starts"))
    if starts and P.monday_of(today) < P.monday_of(starts):
        print(f"This week is before tracking_starts ({cfg['tracking_starts']}) — "
              f"no reminders yet.")
        return

    req = cfg["required_days"]
    threshold = int(cfg["reminders"][WHICH_TO_KEY[which]].get("at_most_active_days",
                                                              1 if which == "wed" else 2))
    tmpl_key = "reminder_first" if which == "wed" else "reminder_second"

    week_monday = P.monday_of(today)
    week_iso = week_monday.isoformat()
    counts = P.active_day_counts(week_iso)
    members = P.load_members()

    already = {(r["week_monday"], r["user_id"], r["which"])
               for r in P.read_rows(P.REMINDER_LOG_FILE)}

    targets = []
    for uid, m in members.items():
        if not P.member_is_trackable(m, week_monday):
            continue
        got = counts.get(uid, 0)
        if got > threshold or got >= req:
            continue
        if not force and (week_iso, str(uid), which) in already:
            continue
        targets.append((uid, got))

    print(f"{which.upper()} reminder — week of {week_iso}: "
          f"{len(targets)} member(s) at or below {threshold}/{req}"
          f"{' (DRY RUN)' if dry_run else ''}")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if dry_run:
        for uid, got in sorted(targets, key=lambda t: t[1]):
            name = P.resolve_name(token, chat_id, uid) if token else f"User {uid}"
            print(f"  would DM {name} ({uid}) — {got}/{req}")
        return

    if not token or not chat_id:
        P._fail("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID must be set for a real send")

    sent = unreachable = 0
    fallback_pings = []
    for uid, got in targets:
        text = P.render(messages, tmpl_key,
                        active_days=min(got, req), required_days=req,
                        clear_weeks=cfg["clear_weeks"])
        ok, reason = P.send_dm(token, uid, text)
        if ok:
            sent += 1
            record = True
        elif "no private chat" in reason:
            unreachable += 1
            record = True
            if cfg.get("dm_fallback_to_group"):
                fallback_pings.append(uid)
        else:
            print(f"  DM to {uid} failed: {reason}", file=sys.stderr)
            record = False   # transient — let a later run retry

        if record:
            P.append_row(P.REMINDER_LOG_FILE, P.REMINDER_LOG_FIELDS, {
                "week_monday": week_iso, "user_id": uid,
                "which": which, "iso_time": P.iso_now(),
            })
        time.sleep(DM_PAUSE_SECONDS)

    if fallback_pings:
        names = " ".join(P.mention_html(u, P.resolve_name(token, chat_id, u))
                         for u in fallback_pings)
        P.send_chat(token, chat_id,
                    f"🎯 Weekly practice check-in — still at fewer than {req} active "
                    f"days this week: {names}\nA voice note, a short video, or a real "
                    f"reply gets you there. 💪")

    print(f"Done. DMs sent: {sent}, unreachable (no bot chat): {unreachable}, "
          f"group fallback: {len(fallback_pings)}.")


if __name__ == "__main__":
    main()
