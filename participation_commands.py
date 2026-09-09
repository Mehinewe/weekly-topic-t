"""
Participation — chat command handlers
=====================================

Parsed and dispatched by log_activity.py for every message that starts with "/".
Member commands work for anyone; the rest require the sender to be a group admin
or listed in config["admin_user_ids"].

Because the poller runs on a ~10-minute cron, replies are not instant.

Commands
--------
Members:
  /pause [weeks] [reason]   request a break (admin-approved by default)
  /mystatus                 your own weekly progress + strike state
  /rules                    the participation rule
  /help

Admins:
  /report                       post a snapshot of this week's progress
  /member <id|reply>            one member's full record
  /list below|strikes|paused|grace|exempt
  /strike add|remove|reset <id|reply> [reason]
  /exempt <id|reply> on|off
  /approve <id> [reason]        decide a pending pause request
  /reject  <id> [reason]
  /endpause <id|reply>
  /remove <id|reply> [reason]   manual kick (ban + unban)
  /set <key> <value>            change a config value (see /settings)
  /setmsg <key> <text>          edit a message template
  /settings                     dump the current config
  /addadmin <id> | /deladmin <id>
"""

import os
from datetime import timedelta

import participation as P


# name -> (dotted config path, type)
SETTABLE = {
    "required_days":           ("required_days", "int"),
    "clear_weeks":             ("clear_weeks", "int"),
    "auto_removal":            ("auto_removal", "bool"),
    # admin_chat_id is intentionally NOT settable here: it would be written back
    # to participation_config.json and committed, exposing the private admin
    # chat id in this public repo. Set the TELEGRAM_ADMIN_CHAT_ID repo secret.
    "week_timezone":           ("week_timezone", "str"),
    "reply_min_words":         ("classifier.reply_min_words", "int"),
    "count_forwarded":         ("classifier.count_forwarded", "bool"),
    "count_voice":             ("classifier.count_voice", "bool"),
    "count_video":             ("classifier.count_video", "bool"),
    "count_topic_reply":       ("classifier.count_topic_reply", "bool"),
    "count_member_reply":      ("classifier.count_member_reply", "bool"),
    "pause_max_weeks":         ("pause.max_weeks", "int"),
    "pause_requires_approval": ("pause.requires_approval", "bool"),
    "pause_max_per_window":    ("pause.max_per_window", "int"),
    "pause_window_days":       ("pause.window_days", "int"),
    "reminders_enabled":       ("enabled.reminders", "bool"),
    "evaluation_enabled":      ("enabled.evaluation", "bool"),
    "welcome_new_members":     ("welcome_new_members", "bool"),
    "dm_fallback_to_group":    ("dm_fallback_to_group", "bool"),
    "grace_extra_weeks":       ("grace_extra_weeks", "int"),
    "max_removals_per_run":    ("max_removals_per_run", "int"),
    "tracking_starts":         ("tracking_starts", "str_or_none"),
}

_TRUE = {"on", "true", "yes", "1", "enable", "enabled"}
_FALSE = {"off", "false", "no", "0", "disable", "disabled"}


class Ctx:
    """Everything a command handler needs, plus dirty flags the poller checks."""

    def __init__(self, token, main_chat_id, cfg, messages, members, admin_ids):
        self.token = token
        self.main_chat_id = main_chat_id
        self.cfg = cfg
        self.messages = messages
        self.members = members
        self.admin_ids = set(admin_ids)
        self.members_changed = False
        self.config_changed = False
        self.messages_changed = False
        self.today = P.today_in_tz(cfg)
        self.week_iso = P.monday_of(self.today).isoformat()


# --- helpers -----------------------------------------------------------

def _reply(ctx, message, text):
    chat_id = (message.get("chat") or {}).get("id", ctx.main_chat_id)
    P.send_chat(ctx.token, chat_id, text)


def _is_admin(ctx, user_id):
    return user_id in ctx.admin_ids or user_id in set(ctx.cfg.get("admin_user_ids", []))


def _target_id(message, args):
    """Numeric id from the first arg, else the replied-to user's id."""
    if args:
        try:
            return int(args[0]), args[1:]
        except ValueError:
            pass
    reply = message.get("reply_to_message") or {}
    ruid = (reply.get("from") or {}).get("id")
    if ruid:
        return int(ruid), args
    return None, args


def _get_or_make_member(ctx, uid):
    m = ctx.members.get(uid)
    if m is None:
        m = P.blank_member(uid, ctx.today.isoformat(), "2000-01-01")
        ctx.members[uid] = m
        ctx.members_changed = True
    return m


def _set_path(cfg, dotted, value):
    parts = dotted.split(".")
    node = cfg
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def _coerce(kind, raw):
    if kind == "int":
        return int(raw)
    if kind == "bool":
        low = raw.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError("expected on/off")
    if kind == "int_or_none":
        if raw.strip().lower() in ("none", "null", ""):
            return None
        return int(raw)
    if kind == "str_or_none":
        return None if raw.strip().lower() in ("none", "null", "") else raw.strip()
    return raw.strip()


def _pause_used_recently(uid, cfg):
    """How many pauses this member had approved inside the abuse window."""
    window = int(cfg["pause"]["window_days"])
    cutoff = (P.datetime.now(P.timezone.utc) - timedelta(days=window))
    n = 0
    for row in P.read_rows(P.PAUSE_REQ_FILE):
        if row.get("user_id") != str(uid) or row.get("status") != "approved":
            continue
        decided = row.get("decided_at") or ""
        try:
            if P.datetime.fromisoformat(decided) >= cutoff:
                n += 1
        except ValueError:
            n += 1  # unparseable date -> count it, be conservative
    return n


def _progress_line(ctx, uid):
    req = ctx.cfg["required_days"]
    got = min(P.active_days_for(uid, ctx.week_iso), req)
    return f"{got}/{req}"


# --- entry point -----------------------------------------------------

def handle_command(message, ctx):
    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return
    head, *rest = text.split(maxsplit=1)
    cmd = head.split("@", 1)[0].lower().lstrip("/")
    args = rest[0].split() if rest else []
    sender = (message.get("from") or {}).get("id")

    member_cmds = {
        "pause": _cmd_pause,
        "mystatus": _cmd_mystatus,
        "status": _cmd_mystatus,
        "rules": _cmd_rules,
        "help": _cmd_help,
    }
    admin_cmds = {
        "report": _cmd_report,
        "member": _cmd_member,
        "list": _cmd_list,
        "strike": _cmd_strike,
        "exempt": _cmd_exempt,
        "approve": _cmd_approve,
        "reject": _cmd_reject,
        "endpause": _cmd_endpause,
        "remove": _cmd_remove,
        "set": _cmd_set,
        "setmsg": _cmd_setmsg,
        "settings": _cmd_settings,
        "addadmin": _cmd_addadmin,
        "deladmin": _cmd_deladmin,
    }

    if cmd in member_cmds:
        member_cmds[cmd](message, ctx, sender, args, raw=rest[0] if rest else "")
    elif cmd in admin_cmds:
        if not _is_admin(ctx, sender):
            _reply(ctx, message, "That command is for admins only.")
            return
        admin_cmds[cmd](message, ctx, sender, args, raw=rest[0] if rest else "")
    # unknown command -> ignore (could be for another feature)


# --- member commands ------------------------------------------------

def _cmd_pause(message, ctx, sender, args, raw=""):
    cfg = ctx.cfg
    max_weeks = int(cfg["pause"]["max_weeks"])
    weeks, reason_parts = max_weeks, args
    if args:
        try:
            weeks = max(1, min(int(args[0]), max_weeks))
            reason_parts = args[1:]
        except ValueError:
            reason_parts = args
    reason = " ".join(reason_parts).strip()

    if _pause_used_recently(sender, cfg) >= int(cfg["pause"]["max_per_window"]):
        _reply(ctx, message, P.render(ctx.messages, "pause_limit",
                                      max_per_window=cfg["pause"]["max_per_window"],
                                      window_days=cfg["pause"]["window_days"]))
        return

    starts = ctx.today
    ends = starts + timedelta(days=7 * weeks)
    auto = not cfg["pause"].get("requires_approval", True)

    P.append_row(P.PAUSE_REQ_FILE, P.PAUSE_REQ_FIELDS, {
        "iso_time": P.iso_now(),
        "user_id": sender,
        "weeks": weeks,
        "reason": reason,
        "status": "approved" if auto else "pending",
        "decided_by": "auto" if auto else "",
        "decided_at": P.iso_now() if auto else "",
        "starts": starts.isoformat() if auto else "",
        "ends": ends.isoformat() if auto else "",
    })

    if auto:
        m = _get_or_make_member(ctx, sender)
        m["status"] = "paused"
        m["pause_start"] = starts.isoformat()
        m["pause_end"] = ends.isoformat()
        m["pauses_used"] = str(P._int(m.get("pauses_used")) + 1)
        ctx.members_changed = True
        _reply(ctx, message, P.render(ctx.messages, "pause_approved",
                                      ends=ends.isoformat(),
                                      required_days=cfg["required_days"]))
        return

    _reply(ctx, message, P.render(ctx.messages, "pause_requested", weeks=weeks))
    name = P.resolve_name(ctx.token, ctx.main_chat_id, sender)
    admin_chat = P.admin_chat_id(cfg, ctx.main_chat_id)
    P.send_chat(ctx.token, admin_chat,
                f"⏸️ <b>Pause request</b>\n{P.mention_html(sender, name)} "
                f"(<code>{sender}</code>) — {weeks} week(s)\n"
                f"Reason: {reason or '—'}\n\n"
                f"Approve: <code>/approve {sender}</code>   "
                f"Reject: <code>/reject {sender}</code>")


def _cmd_mystatus(message, ctx, sender, args, raw=""):
    m = ctx.members.get(sender)
    cfg = ctx.cfg
    status = (m.get("status") if m else "active") or "active"
    _reply(ctx, message, P.render(ctx.messages, "my_status",
                                  active_days=_progress_line(ctx, sender).split("/")[0],
                                  required_days=cfg["required_days"],
                                  strikes=(m.get("strikes") if m else "0") or "0",
                                  streak=(m.get("success_streak") if m else "0") or "0",
                                  clear_weeks=cfg["clear_weeks"],
                                  status=status))


def _cmd_rules(message, ctx, sender, args, raw=""):
    cfg = ctx.cfg
    _reply(ctx, message, P.render(ctx.messages, "rules",
                                  required_days=cfg["required_days"],
                                  clear_weeks=cfg["clear_weeks"],
                                  reply_min_words=cfg["classifier"]["reply_min_words"],
                                  pause_max_weeks=cfg["pause"]["max_weeks"]))


def _cmd_help(message, ctx, sender, args, raw=""):
    lines = ["<b>Participation commands</b>",
             "/rules — the weekly rule",
             "/mystatus — your progress this week",
             "/pause [weeks] [reason] — request a break"]
    if _is_admin(ctx, sender):
        lines += ["",
                  "<b>Admin</b>",
                  "/report  /member &lt;id&gt;  /list below|strikes|paused|grace",
                  "/strike add|remove|reset &lt;id&gt;  /exempt &lt;id&gt; on|off",
                  "/approve &lt;id&gt;  /reject &lt;id&gt;  /endpause &lt;id&gt;  /remove &lt;id&gt;",
                  "/set &lt;key&gt; &lt;value&gt;  /setmsg &lt;key&gt; &lt;text&gt;  /settings"]
    _reply(ctx, message, "\n".join(lines))


# --- admin commands -----------------------------------------------

def _cmd_report(message, ctx, sender, args, raw=""):
    cfg, req = ctx.cfg, ctx.cfg["required_days"]
    counts = P.active_day_counts(ctx.week_iso)
    tracked = [uid for uid, m in ctx.members.items()
               if P.member_is_trackable(m, P.monday_of(ctx.today))]
    done = sum(1 for uid in tracked if counts.get(uid, 0) >= req)
    behind = [uid for uid in tracked if counts.get(uid, 0) < req]
    paused = [uid for uid, m in ctx.members.items() if (m.get("status") == "paused")]
    strikes = [(uid, m) for uid, m in ctx.members.items() if P._int(m.get("strikes")) >= 1]

    lines = [f"📊 <b>Progress — week of {ctx.week_iso}</b> (in progress)",
             f"Tracked members: {len(tracked)}",
             f"✅ At {req}/{req}: {done}",
             f"⚠️ Below {req}: {len(behind)}",
             f"⏸️ Paused: {len(paused)}",
             f"🚩 Carrying a strike: {len(strikes)}"]
    if behind:
        lines.append("\n<b>Below target</b>")
        for uid in sorted(behind, key=lambda u: counts.get(u, 0)):
            name = P.resolve_name(ctx.token, ctx.main_chat_id, uid)
            lines.append(f"• {name} (<code>{uid}</code>) — {min(counts.get(uid,0), req)}/{req}")
    _reply(ctx, message, "\n".join(lines))


def _cmd_member(message, ctx, sender, args, raw=""):
    uid, _ = _target_id(message, args)
    if uid is None:
        _reply(ctx, message, "Usage: /member &lt;user_id&gt; (or reply to their message)")
        return
    m = ctx.members.get(uid)
    if not m:
        _reply(ctx, message, f"No record for <code>{uid}</code>.")
        return
    name = P.resolve_name(ctx.token, ctx.main_chat_id, uid)
    prog = _progress_line(ctx, uid)
    _reply(ctx, message,
           f"<b>{name}</b> (<code>{uid}</code>)\n"
           f"Status: {m.get('status') or 'active'}\n"
           f"This week: {prog}\n"
           f"Strikes: {m.get('strikes') or '0'}  "
           f"(since {m.get('strike1_week') or '—'})\n"
           f"Success streak: {m.get('success_streak') or '0'}/{ctx.cfg['clear_weeks']}\n"
           f"Joined: {m.get('joined_at') or '—'}  Grace until: {m.get('grace_until') or '—'}\n"
           f"Pause: {m.get('pause_start') or '—'} → {m.get('pause_end') or '—'}\n"
           f"Last active week: {m.get('last_active_week') or '—'}")


def _cmd_list(message, ctx, sender, args, raw=""):
    which = (args[0].lower() if args else "below")
    req = ctx.cfg["required_days"]
    counts = P.active_day_counts(ctx.week_iso)
    week_monday = P.monday_of(ctx.today)

    def pick():
        for uid, m in ctx.members.items():
            st = m.get("status") or "active"
            if which == "below" and P.member_is_trackable(m, week_monday) and counts.get(uid, 0) < req:
                yield uid, f"{min(counts.get(uid,0), req)}/{req}"
            elif which == "strikes" and P._int(m.get("strikes")) >= 1:
                yield uid, f"strike {m.get('strikes')} (streak {m.get('success_streak') or 0})"
            elif which == "paused" and st == "paused":
                yield uid, f"until {m.get('pause_end') or '?'}"
            elif which == "grace" and st != "removed" and P.parse_date(m.get("grace_until")) and week_monday < P.parse_date(m.get("grace_until")):
                yield uid, f"until {m.get('grace_until')}"
            elif which == "exempt" and st == "exempt":
                yield uid, "exempt"

    rows = list(pick())
    if not rows:
        _reply(ctx, message, f"Nobody in list '{which}'.")
        return
    lines = [f"<b>{which}</b> ({len(rows)})"]
    for uid, extra in rows[:60]:
        name = P.resolve_name(ctx.token, ctx.main_chat_id, uid)
        lines.append(f"• {name} (<code>{uid}</code>) — {extra}")
    _reply(ctx, message, "\n".join(lines))


def _cmd_strike(message, ctx, sender, args, raw=""):
    if not args or args[0].lower() not in ("add", "remove", "reset"):
        _reply(ctx, message, "Usage: /strike add|remove|reset &lt;user_id&gt;")
        return
    action = args[0].lower()
    uid, _ = _target_id(message, args[1:])
    if uid is None:
        _reply(ctx, message, "Give a user id or reply to the member's message.")
        return
    m = _get_or_make_member(ctx, uid)
    n = P._int(m.get("strikes"))
    if action == "add":
        n = min(n + 1, 2)
        m["strikes"] = str(n)
        if n == 1 and not m.get("strike1_week"):
            m["strike1_week"] = ctx.week_iso
        m["success_streak"] = "0"
        tail = "  (now at Strike 2 — use /remove to remove them)" if n == 2 else ""
        msg = f"Strike added → {n}.{tail}"
    elif action == "remove":
        n = max(n - 1, 0)
        m["strikes"] = str(n)
        if n == 0:
            m["strike1_week"] = ""
            m["success_streak"] = "0"
        msg = f"Strike removed → {n}."
    else:  # reset
        m["strikes"] = "0"
        m["strike1_week"] = ""
        m["success_streak"] = "0"
        if m.get("status") == "removed":
            m["status"] = "active"
        msg = "Strikes reset to 0."
    ctx.members_changed = True
    _reply(ctx, message, f"{P.resolve_name(ctx.token, ctx.main_chat_id, uid)}: {msg}")


def _cmd_exempt(message, ctx, sender, args, raw=""):
    uid, rest = _target_id(message, args)
    on = (rest[0].lower() in _TRUE) if rest else True
    if uid is None:
        _reply(ctx, message, "Usage: /exempt &lt;user_id&gt; on|off")
        return
    m = _get_or_make_member(ctx, uid)
    if on:
        m["status"] = "exempt"
        txt = "now exempt from participation rules."
    else:
        if m.get("status") == "exempt":
            m["status"] = "active"
        txt = "no longer exempt."
    ctx.members_changed = True
    _reply(ctx, message, f"{P.resolve_name(ctx.token, ctx.main_chat_id, uid)} {txt}")


def _decide_pause(ctx, message, sender, uid, approve, reason):
    reqs = P.read_rows(P.PAUSE_REQ_FILE)
    pending = [r for r in reqs if r.get("user_id") == str(uid) and r.get("status") == "pending"]
    if not pending:
        _reply(ctx, message, f"No pending pause request for <code>{uid}</code>.")
        return
    row = pending[-1]
    weeks = P._int(row.get("weeks"), ctx.cfg["pause"]["max_weeks"])
    starts = ctx.today
    ends = starts + timedelta(days=7 * weeks)

    for r in reqs:
        if r is row:
            r["status"] = "approved" if approve else "rejected"
            r["decided_by"] = str(sender)
            r["decided_at"] = P.iso_now()
            if approve:
                r["starts"], r["ends"] = starts.isoformat(), ends.isoformat()
            elif reason:
                r["reason"] = (r.get("reason") or "") + f" | rejected: {reason}"
    P.write_rows(P.PAUSE_REQ_FILE, P.PAUSE_REQ_FIELDS, reqs)

    m = _get_or_make_member(ctx, uid)
    if approve:
        m["status"] = "paused"
        m["pause_start"] = starts.isoformat()
        m["pause_end"] = ends.isoformat()
        m["pauses_used"] = str(P._int(m.get("pauses_used")) + 1)
        dm_ok, _ = P.send_dm(ctx.token, uid,
                             P.render(ctx.messages, "pause_approved",
                                      ends=ends.isoformat(),
                                      required_days=ctx.cfg["required_days"]))
        _reply(ctx, message, f"✅ Pause approved for {P.resolve_name(ctx.token, ctx.main_chat_id, uid)} "
                             f"until {ends.isoformat()}." + ("" if dm_ok else " (couldn't DM them)"))
    else:
        P.send_dm(ctx.token, uid, P.render(ctx.messages, "pause_rejected", reason=reason or ""))
        _reply(ctx, message, f"Pause rejected for {P.resolve_name(ctx.token, ctx.main_chat_id, uid)}.")
    ctx.members_changed = True


def _cmd_approve(message, ctx, sender, args, raw=""):
    uid, rest = _target_id(message, args)
    if uid is None:
        _reply(ctx, message, "Usage: /approve &lt;user_id&gt;")
        return
    _decide_pause(ctx, message, sender, uid, True, " ".join(rest))


def _cmd_reject(message, ctx, sender, args, raw=""):
    uid, rest = _target_id(message, args)
    if uid is None:
        _reply(ctx, message, "Usage: /reject &lt;user_id&gt; [reason]")
        return
    _decide_pause(ctx, message, sender, uid, False, " ".join(rest))


def _cmd_endpause(message, ctx, sender, args, raw=""):
    uid, _ = _target_id(message, args)
    if uid is None or uid not in ctx.members:
        _reply(ctx, message, "Usage: /endpause &lt;user_id&gt;")
        return
    m = ctx.members[uid]
    m["status"] = "active"
    m["pause_start"] = m["pause_end"] = ""
    ctx.members_changed = True
    P.send_dm(ctx.token, uid, P.render(ctx.messages, "pause_ended",
                                       required_days=ctx.cfg["required_days"]))
    _reply(ctx, message, f"Pause ended for {P.resolve_name(ctx.token, ctx.main_chat_id, uid)}.")


def _cmd_remove(message, ctx, sender, args, raw=""):
    uid, rest = _target_id(message, args)
    if uid is None:
        _reply(ctx, message, "Usage: /remove &lt;user_id&gt; [reason]")
        return
    reason = " ".join(rest) or "manual removal by admin"
    dm_ok, _ = P.send_dm(ctx.token, uid, P.render(ctx.messages, "removal_notice"))
    ok, detail = P.kick_member(ctx.token, ctx.main_chat_id, uid)
    if not ok:
        _reply(ctx, message, f"Couldn't remove <code>{uid}</code>: {detail}")
        return
    m = _get_or_make_member(ctx, uid)
    m["status"] = "removed"
    m["removed_at"] = P.iso_now()
    m["removal_count"] = str(P._int(m.get("removal_count")) + 1)
    ctx.members_changed = True
    P.append_row(P.REMOVALS_FILE, P.REMOVAL_FIELDS, {
        "iso_time": P.iso_now(), "user_id": uid, "reason": reason,
        "week_monday": ctx.week_iso, "strikes": m.get("strikes") or "",
        "dm_delivered": int(dm_ok),
    })
    _reply(ctx, message, f"Removed {P.resolve_name(ctx.token, ctx.main_chat_id, uid)} "
                         f"(<code>{uid}</code>)." + ("" if dm_ok else " (couldn't DM them)"))


def _cmd_set(message, ctx, sender, args, raw=""):
    if len(args) < 2 or args[0] not in SETTABLE:
        _reply(ctx, message, "Usage: /set &lt;key&gt; &lt;value&gt;\nKeys: " +
               ", ".join(sorted(SETTABLE)))
        return
    path, kind = SETTABLE[args[0]]
    try:
        value = _coerce(kind, " ".join(args[1:]))
    except ValueError as exc:
        _reply(ctx, message, f"Bad value: {exc}")
        return
    _set_path(ctx.cfg, path, value)
    ctx.config_changed = True
    _reply(ctx, message, f"✅ {args[0]} = {value!r}")


def _cmd_setmsg(message, ctx, sender, args, raw=""):
    if len(args) < 2:
        _reply(ctx, message, "Usage: /setmsg &lt;key&gt; &lt;new text&gt;\nKeys: " +
               ", ".join(k for k in ctx.messages if not k.startswith("_")))
        return
    key = args[0]
    if key not in ctx.messages:
        _reply(ctx, message, f"Unknown message key '{key}'.")
        return
    ctx.messages[key] = raw.split(maxsplit=1)[1] if " " in raw else ""
    ctx.messages_changed = True
    _reply(ctx, message, f"✅ Updated message '{key}'. Preview:\n\n" + ctx.messages[key][:600])


def _cmd_settings(message, ctx, sender, args, raw=""):
    c = ctx.cfg
    _reply(ctx, message,
           "<b>Participation settings</b>\n"
           f"required_days: {c['required_days']}\n"
           f"clear_weeks: {c['clear_weeks']}\n"
           f"auto_removal: {c['auto_removal']}\n"
           f"week_timezone: {c['week_timezone']}\n"
           f"reply_min_words: {c['classifier']['reply_min_words']}\n"
           f"count_forwarded: {c['classifier']['count_forwarded']}\n"
           f"pause.max_weeks: {c['pause']['max_weeks']}\n"
           f"pause.requires_approval: {c['pause']['requires_approval']}\n"
           f"pause.max_per_window: {c['pause']['max_per_window']} / {c['pause']['window_days']}d\n"
           f"reminders enabled: {c['enabled']['reminders']}\n"
           f"evaluation enabled: {c['enabled']['evaluation']}\n"
           f"admin_chat_id: {os.environ.get('TELEGRAM_ADMIN_CHAT_ID') or c['admin_chat_id'] or '(main group)'}\n"
           f"admin_user_ids: {c['admin_user_ids']}")


def _cmd_addadmin(message, ctx, sender, args, raw=""):
    try:
        uid = int(args[0])
    except (IndexError, ValueError):
        _reply(ctx, message, "Usage: /addadmin &lt;user_id&gt;")
        return
    lst = ctx.cfg.setdefault("admin_user_ids", [])
    if uid not in lst:
        lst.append(uid)
        ctx.config_changed = True
    _reply(ctx, message, f"admin_user_ids = {lst}")


def _cmd_deladmin(message, ctx, sender, args, raw=""):
    try:
        uid = int(args[0])
    except (IndexError, ValueError):
        _reply(ctx, message, "Usage: /deladmin &lt;user_id&gt;")
        return
    lst = ctx.cfg.setdefault("admin_user_ids", [])
    if uid in lst:
        lst.remove(uid)
        ctx.config_changed = True
    _reply(ctx, message, f"admin_user_ids = {lst}")
