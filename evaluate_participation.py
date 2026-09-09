"""
Participation — weekly evaluation (Monday)
=========================================

Runs after a week closes. For every tracked member it computes active days for
the finished week, advances the strike state machine, sends Strike 1 warnings /
"strike cleared" notes / removal notices, kicks members who hit Strike 2 (only
when auto_removal is on), and posts a summary to the admin chat.

    python evaluate_participation.py --dry-run     # full preview, sends/changes nothing
    python evaluate_participation.py               # real run for LAST week
    python evaluate_participation.py --week 2026-07-06
    python evaluate_participation.py --this-week   # evaluate the current week (testing)
    python evaluate_participation.py --force       # re-run a week already in the eval log

Safety gates (a broken logger must not clear the group):
  * if the week has no logged practice activity at all -> abort
  * if far fewer members were active than usual -> abort unless --force
  * never remove more than max_removals_per_run members in one run -> abort

Idempotency: the finished week is recorded in participation_eval_log.csv (committed
back by the workflow); a repeat/backup run for the same week no-ops.
"""

import os
import sys
import time
from datetime import timedelta

import participation as P

DM_PAUSE_SECONDS = 1.1


def _load_eval_log():
    return {r["week_monday"] for r in P.read_rows(P.EVAL_LOG_FILE)}


def main():
    P.load_dotenv()
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    force = "--force" in args
    this_week = "--this-week" in args

    cfg = P.load_config()
    messages = P.load_messages()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not cfg["enabled"].get("evaluation", True) and not force:
        print("Evaluation is disabled in participation_config.json — nothing to do.")
        return

    today = P.today_in_tz(cfg)
    if "--week" in args:
        w = P.parse_date(args[args.index("--week") + 1])
        if not w:
            P._fail("bad --week date; use YYYY-MM-DD")
        target_monday = P.monday_of(w)
    elif this_week:
        target_monday = P.monday_of(today)
    else:
        target_monday = P.monday_of(today) - timedelta(days=7)
    week_iso = target_monday.isoformat()

    if not force and week_iso in _load_eval_log():
        print(f"Week {week_iso} already evaluated — skipping (use --force to re-run).")
        return

    starts = P.parse_date(cfg.get("tracking_starts"))
    if starts and target_monday < P.monday_of(starts) and not force:
        print(f"Week {week_iso} is before tracking_starts ({cfg['tracking_starts']}) "
              f"— skipping. Nobody is struck for weeks before enforcement began.")
        return

    req = cfg["required_days"]
    counts = P.active_day_counts(week_iso)
    members = P.load_members()
    if not members:
        members = P.seed_members_from_activity(cfg)   # first run before the poller
    tracked = [(uid, m) for uid, m in members.items()
               if P.member_is_trackable(m, target_monday)]

    # ---- safety gate: does the activity log look healthy for this week? ----
    active_users = len(counts)
    if active_users == 0 and tracked:
        _abort(token, cfg, chat_id, dry_run,
               f"No practice activity logged for week {week_iso} at all. "
               f"The logger or the bot's privacy setting is probably broken. "
               f"Aborting so nobody is wrongly struck.")
    if not force and len(tracked) >= 10 and active_users < 3:
        _abort(token, cfg, chat_id, dry_run,
               f"Only {active_users} member(s) show any activity for week {week_iso} "
               f"out of {len(tracked)} tracked — that looks like a logging gap. "
               f"Aborting (re-run with --force if the week really was that quiet).")

    # ---- pass 1: decide, don't mutate — count pending removals ----
    would_remove = 0
    for uid, m in tracked:
        passed = counts.get(uid, 0) >= req
        strikes = P._int(m.get("strikes"))
        if not passed and cfg.get("auto_removal") and strikes >= 1:
            would_remove += 1
    cap = int(cfg.get("max_removals_per_run", 5))
    if would_remove > cap:
        _abort(token, cfg, chat_id, dry_run,
               f"Week {week_iso} would remove {would_remove} members (cap is {cap}). "
               f"Aborting — check the logs, then raise max_removals_per_run or run "
               f"with --force intentionally.")

    # ---- pass 2: apply ----
    tally = {"completed": 0, "below": 0, "strike1": 0, "cleared": 0,
             "removed": 0, "flagged": 0, "streak": 0}
    strike_lines, removed_lines, skipped = [], [], 0
    removals_done = 0

    for uid, m in tracked:
        if m.get("status") == "grace":   # grace period is over — start tracking
            m["status"] = "active"
        active = counts.get(uid, 0)
        passed = active >= req
        tally["completed" if passed else "below"] += 1

        action = P.apply_week_result(m, passed, week_iso, cfg)
        name = P.resolve_name(token, chat_id, uid) if token else f"User {uid}"

        if action == "strike1":
            tally["strike1"] += 1
            strike_lines.append(f"• {name} (<code>{uid}</code>) — {min(active, req)}/{req}")
            if not dry_run:
                _dm(token, cfg, messages, uid, "warning_strike1",
                    active_days=min(active, req), required_days=req,
                    clear_weeks=cfg["clear_weeks"])
        elif action == "cleared":
            tally["cleared"] += 1
            if not dry_run:
                _dm(token, cfg, messages, uid, "strike_cleared",
                    clear_weeks=cfg["clear_weeks"])
        elif action == "streak":
            tally["streak"] += 1
        elif action == "remove":
            if not dry_run:
                _dm(token, cfg, messages, uid, "removal_notice")
                ok, detail = P.kick_member(token, chat_id, uid)
                if ok:
                    m["status"] = "removed"
                    m["removed_at"] = P.iso_now()
                    m["removal_count"] = str(P._int(m.get("removal_count")) + 1)
                    P.append_row(P.REMOVALS_FILE, P.REMOVAL_FIELDS, {
                        "iso_time": P.iso_now(), "user_id": uid,
                        "reason": "Strike 2 — missed a week while Strike 1 active",
                        "week_monday": week_iso, "strikes": "2", "dm_delivered": "",
                    })
                    removals_done += 1
                    tally["removed"] += 1
                    removed_lines.append(f"• {name} (<code>{uid}</code>)")
                else:
                    print(f"  kick failed for {uid}: {detail}", file=sys.stderr)
                    tally["flagged"] += 1
                    removed_lines.append(f"• {name} (<code>{uid}</code>) — KICK FAILED: {detail}")
            else:
                tally["removed"] += 1
                removed_lines.append(f"• {name} (<code>{uid}</code>)  [dry run]")
        elif action == "strike2_flagged":
            tally["flagged"] += 1
            removed_lines.append(f"• {name} (<code>{uid}</code>) — at Strike 2, "
                                 f"auto-removal OFF (use /remove {uid})")
        if not dry_run:
            time.sleep(DM_PAUSE_SECONDS if action in
                       ("strike1", "cleared", "remove") else 0)

        P.append_row(P.WEEKLY_RESULT_FILE, P.WEEKLY_RESULT_FIELDS, {
            "week_monday": week_iso, "user_id": uid, "active_days": active,
            "passed": int(passed), "strikes_after": m.get("strikes"),
            "streak_after": m.get("success_streak"), "action": action,
            "iso_time": P.iso_now(),
        }) if not dry_run else None

    paused = sum(1 for _, m in members.items() if (m.get("status") == "paused"))
    in_grace = sum(1 for uid, m in members.items()
                   if (m.get("status") != "removed")
                   and P.parse_date(m.get("grace_until"))
                   and target_monday < P.parse_date(m.get("grace_until")))

    report = _build_report(week_iso, len(tracked), tally, paused, in_grace,
                           strike_lines, removed_lines, cfg, dry_run)
    print("\n" + report.replace("<b>", "").replace("</b>", "")
          .replace("<code>", "").replace("</code>", ""))

    if dry_run:
        print("\n--- DRY RUN — no messages sent, no state changed ---")
        return

    P.save_members(members)
    P.append_row(P.EVAL_LOG_FILE, P.EVAL_LOG_FIELDS,
                 {"week_monday": week_iso, "iso_time": P.iso_now()})
    if token:
        P.send_chat(token, P.admin_chat_id(cfg, chat_id), report)
    print(f"\nDone. Removals this run: {removals_done}.")


def _dm(token, cfg, messages, uid, key, **values):
    ok, reason = P.send_dm(token, uid, P.render(messages, key, **values))
    if not ok and cfg.get("dm_fallback_to_group"):
        # a warning nobody can see isn't a warning — put it in the group
        name = P.resolve_name(token, os.environ.get("TELEGRAM_CHAT_ID"), uid)
        P.send_chat(token, os.environ.get("TELEGRAM_CHAT_ID"),
                    f"{P.mention_html(uid, name)} — " + P.render(messages, key, **values))
    return ok


def _build_report(week_iso, tracked, t, paused, grace, strike_lines, removed_lines,
                  cfg, dry_run):
    head = "🧪 " if dry_run else "📊 "
    lines = [
        f"{head}<b>Weekly Participation Report — week of {week_iso}</b>",
        "",
        f"Tracked members: {tracked}",
        f"✅ Completed {cfg['required_days']}/{cfg['required_days']}: {t['completed']}",
        f"⚠️ Below requirement: {t['below']}",
        f"🚩 New Strike 1: {t['strike1']}",
        f"❌ Removed (Strike 2): {t['removed']}",
        f"🟡 Flagged at Strike 2 (auto-removal off): {t['flagged']}",
        f"🎉 Strikes cleared: {t['cleared']}",
        f"⏸️ Currently paused: {paused}",
        f"🌱 In new-member grace: {grace}",
    ]
    if strike_lines:
        lines += ["", "<b>New Strike 1</b>"] + strike_lines
    if removed_lines:
        lines += ["", "<b>Removed / flagged</b>"] + removed_lines
    if not cfg.get("auto_removal"):
        lines += ["", "<i>auto_removal is OFF — no one is kicked automatically. "
                  "Turn it on with /set auto_removal on.</i>"]
    return "\n".join(lines)


def _abort(token, cfg, chat_id, dry_run, why):
    print(f"ABORT: {why}", file=sys.stderr)
    if token and not dry_run:
        P.send_chat(token, P.admin_chat_id(cfg, chat_id),
                    f"🛑 <b>Weekly evaluation aborted</b>\n{why}")
    sys.exit(1)


if __name__ == "__main__":
    main()
