"""
Group activity logger + participation ingest
============================================

One process owns Telegram's getUpdates cursor, so everything that reads incoming
updates lives here. Each run:

  1. Reads the last processed update id from `activity_state.json`.
  2. Calls getUpdates for everything newer (messages + chat_member events).
  3. For each group message:
       * appends one row to `activity_log.csv` (feeds the weekly awards), now
         with a `practice` 0/1 column for the participation rule;
       * updates the member's row in `members.csv` (roster / last seen).
  4. Handles membership changes: welcomes new joiners (with a grace period),
     marks leavers.
  5. Dispatches "/" commands (`/pause`, admin commands) — see
     participation_commands.py.
  6. Expires finished pauses and ends new-member grace periods.
  7. Saves the new update id + all changed state files back.

The GitHub Actions workflow (`.github/workflows/log.yml`) commits the updated
files back to the repo so state survives between stateless runs. Everything is
keyed by numeric Telegram id — no names are stored.

IMPORTANT: the bot must SEE every group message — disable privacy mode in
@BotFather (or make the bot an admin). For removals it also needs to be an admin
with the "ban users" right.

Required environment variables:
    TELEGRAM_BOT_TOKEN   the token from @BotFather
    TELEGRAM_CHAT_ID     the group's chat id (negative, e.g. -1001234567890)
"""

import json
import os
import sys
from datetime import datetime, timezone

import requests

import participation as P
import participation_commands as PC

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

BASE_DIR = P.BASE_DIR
LOG_FILE = P.ACTIVITY_LOG
STATE_FILE = BASE_DIR / "activity_state.json"

API_TIMEOUT = 60
GETUPDATES_LIMIT = 100

LOG_FIELDS = P.ACTIVITY_FIELDS  # iso_time, week_monday, user_id, type, is_reply, reply_to_user_id, practice

# Update types we ask Telegram for. chat_member only arrives if the bot is an
# admin; requesting it when it isn't is harmless.
ALLOWED_UPDATES = ["message", "chat_member", "my_chat_member"]


# --- unchanged helpers --------------------------------------------------

def load_dotenv():
    P.load_dotenv()


def _fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def monday_of(d):
    return P.monday_of(d)


def read_offset():
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    last = data.get("last_update_id")
    return (last + 1) if isinstance(last, int) else None


def write_offset(last_update_id):
    STATE_FILE.write_text(
        json.dumps({"last_update_id": last_update_id}, indent=2) + "\n",
        encoding="utf-8",
    )


def classify(message):
    """Primary activity type for the awards tally (unchanged)."""
    if "video" in message or "video_note" in message:
        return "video"
    if "voice" in message:
        return "voice"
    if "text" in message:
        return "text"
    return "other"


def fetch_updates(token, offset):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"limit": GETUPDATES_LIMIT, "timeout": 0,
              "allowed_updates": json.dumps(ALLOWED_UPDATES)}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(url, params=params, timeout=API_TIMEOUT)
    try:
        body = resp.json()
    except ValueError:
        _fail(f"getUpdates: non-JSON response (HTTP {resp.status_code})")
    if not body.get("ok"):
        _fail(f"getUpdates failed: {body.get('description', resp.text[:300])}")
    return body.get("result", [])


# --- participation: roster helpers -----------------------------------

JOIN_STATUSES = {"member", "administrator", "creator", "restricted"}
LEAVE_STATUSES = {"left", "kicked"}


def touch_member(members, uid, iso_time, cfg, today, changed):
    """Note that `uid` sent a message. Creates a row for a member we hadn't
    seen before (a pre-existing member who just spoke for the first time)."""
    m = members.get(uid)
    if m is None:
        m = P.blank_member(uid, today.isoformat(), "2000-01-01")  # already established
        members[uid] = m
        changed.add("members")
    m["last_seen"] = iso_time
    if (m.get("status") or "active") in ("left", "removed"):
        # they're clearly in the group and active again
        m["status"] = "active"
        m["removed_at"] = ""
        changed.add("members")
    return m


def on_join(members, user, today, cfg, join_queue, changed):
    uid = user.get("id")
    if uid is None or user.get("is_bot"):
        return
    existing = members.get(uid)
    grace = P.grace_until_for(today, cfg)
    if existing and (existing.get("status") or "active") not in ("left", "removed"):
        return  # already an active member — nothing to do
    if existing:
        existing.update(status="grace", joined_at=today.isoformat(),
                        grace_until=grace, removed_at="")
    else:
        m = P.blank_member(uid, today.isoformat(), grace)
        m["status"] = "grace"
        members[uid] = m
    changed.add("members")
    join_queue.append(uid)


def on_leave(members, user, changed):
    uid = user.get("id")
    m = members.get(uid) if uid is not None else None
    if not m:
        return
    if m.get("status") != "removed":   # keep our own removal record intact
        m["status"] = "left"
        changed.add("members")


def sweep_members(members, cfg, token, messages, today, changed):
    """End finished pauses and new-member grace periods."""
    for uid, m in members.items():
        status = (m.get("status") or "active").strip()
        if status == "paused":
            end = P.parse_date(m.get("pause_end"))
            if end and today >= end:
                m["status"] = "active"
                m["pause_start"] = m["pause_end"] = ""
                changed.add("members")
                P.send_dm(token, uid, P.render(messages, "pause_ended",
                                               required_days=cfg["required_days"]))
        elif status == "grace":
            g = P.parse_date(m.get("grace_until"))
            if g and today >= g:
                m["status"] = "active"
                changed.add("members")


# --- main -------------------------------------------------------------

def main():
    load_dotenv()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token:
        _fail("TELEGRAM_BOT_TOKEN is not set")
    if not chat_id:
        _fail("TELEGRAM_CHAT_ID is not set")
    try:
        target_chat = int(chat_id)
    except ValueError:
        _fail(f"TELEGRAM_CHAT_ID is not a number: {chat_id!r}")

    cfg = P.load_config()
    messages = P.load_messages()
    P.migrate_activity_log()
    topic_ids = P.load_topic_message_ids()

    changed = set()  # which state files to write: "members", "config", "messages"
    members = P.load_members()
    if not members:
        members = P.seed_members_from_activity(cfg)
        changed.add("members")

    today = P.today_in_tz(cfg)

    offset = read_offset()
    new_rows = []
    command_msgs = []
    join_queue = []
    highest_id = None

    while True:
        updates = fetch_updates(token, offset)
        if not updates:
            break

        for upd in updates:
            update_id = upd.get("update_id")
            if isinstance(update_id, int):
                highest_id = update_id if highest_id is None else max(highest_id, update_id)
                offset = update_id + 1

            # --- bot's own membership changed (added / promoted / removed) ---
            mcm = upd.get("my_chat_member")
            if mcm:
                new_status = (mcm.get("new_chat_member") or {}).get("status")
                print(f"my_chat_member: bot is now '{new_status}' in "
                      f"{(mcm.get('chat') or {}).get('id')}")
                continue

            # --- a member joined / left / was promoted ---
            cm = upd.get("chat_member")
            if cm:
                if (cm.get("chat") or {}).get("id") != target_chat:
                    continue
                old_s = (cm.get("old_chat_member") or {}).get("status")
                new_s = (cm.get("new_chat_member") or {}).get("status")
                user = (cm.get("new_chat_member") or {}).get("user") or {}
                if new_s in JOIN_STATUSES and old_s in (LEAVE_STATUSES | {None}):
                    on_join(members, user, today, cfg, join_queue, changed)
                elif new_s in LEAVE_STATUSES and old_s not in LEAVE_STATUSES:
                    on_leave(members, user, changed)
                continue

            message = upd.get("message")
            if not message:
                continue
            msg_chat = (message.get("chat") or {}).get("id")
            # TELEGRAM_ADMIN_CHAT_ID (repo secret) wins over the config value so
            # the private admin group's id stays out of the public repo.
            admin_chat_raw = os.environ.get("TELEGRAM_ADMIN_CHAT_ID") or cfg.get("admin_chat_id")
            try:
                admin_chat = int(admin_chat_raw) if admin_chat_raw is not None else None
            except (TypeError, ValueError):
                admin_chat = admin_chat_raw
            in_main = msg_chat == target_chat
            in_admin_chat = admin_chat is not None and msg_chat == admin_chat
            if not (in_main or in_admin_chat):
                continue

            sender = message.get("from") or {}
            if sender.get("is_bot"):
                continue
            uid = sender.get("id")

            # Commands are accepted from the main group OR the admin chat; they
            # are handled after the drain and never count as activity.
            text = message.get("text") or ""
            if uid is not None and text.lstrip().startswith("/"):
                if in_main:   # a command sender in the group is still a member
                    sent = datetime.fromtimestamp(message.get("date", 0), tz=timezone.utc)
                    touch_member(members, uid, sent.isoformat(), cfg, today, changed)
                command_msgs.append(message)
                continue

            # Everything below is main-group only (activity, roster, joins).
            if not in_main:
                continue

            # --- service messages: joins / leaves / pins etc. ---
            if message.get("new_chat_members"):
                for u in message["new_chat_members"]:
                    on_join(members, u, today, cfg, join_queue, changed)
                continue
            if message.get("left_chat_member"):
                on_leave(members, message["left_chat_member"], changed)
                continue
            if any(k in message for k in P.SERVICE_KEYS):
                continue

            if uid is None:
                continue

            sent = datetime.fromtimestamp(message.get("date", 0), tz=timezone.utc)
            touch_member(members, uid, sent.isoformat(), cfg, today, changed)

            reply = message.get("reply_to_message") or {}
            reply_user = (reply.get("from") or {}).get("id")
            is_reply = bool(reply) and reply_user not in (None, uid)
            practice, _reason = P.classify_practice(message, cfg, topic_ids)

            new_rows.append({
                "iso_time": sent.isoformat(),
                "week_monday": monday_of(sent.date()).isoformat(),
                "user_id": uid,
                "type": classify(message),
                "is_reply": int(is_reply),
                "reply_to_user_id": reply_user if is_reply else "",
                "practice": int(bool(practice)),
            })
            if practice:
                wk = monday_of(sent.date()).isoformat()
                m = members.get(uid)
                if m and m.get("last_active_week") != wk:
                    m["last_active_week"] = wk
                    changed.add("members")

        if len(updates) < GETUPDATES_LIMIT:
            break

    # --- pauses / grace periods that have elapsed ---
    sweep_members(members, cfg, token, messages, today, changed)

    # --- dispatch commands ---
    if command_msgs:
        admin_ids = P.get_admin_ids(token, target_chat)
        ctx = PC.Ctx(token, target_chat, cfg, messages, members, admin_ids)
        for msg in command_msgs:
            try:
                PC.handle_command(msg, ctx)
            except Exception as exc:   # never let one bad command abort the run
                print(f"command error ({msg.get('text','')!r}): {exc}", file=sys.stderr)
        if ctx.members_changed:
            changed.add("members")
        if ctx.config_changed:
            changed.add("config")
        if ctx.messages_changed:
            changed.add("messages")

    # --- welcome new joiners ---
    if cfg.get("welcome_new_members", True) and messages.get("welcome"):
        for uid in join_queue:
            name = P.resolve_name(token, target_chat, uid)
            P.send_chat(token, target_chat, P.render(
                messages, "welcome",
                name=P.mention_html(uid, name),
                required_days=cfg["required_days"],
                clear_weeks=cfg["clear_weeks"],
                pause_max_weeks=cfg["pause"]["max_weeks"],
            ))

    # --- persist ---
    if new_rows:
        write_header = not LOG_FILE.exists()
        with LOG_FILE.open("a", newline="", encoding="utf-8") as f:
            import csv
            writer = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)

    if "members" in changed:
        P.save_members(members)
    if "config" in changed:
        P.save_config(cfg)
    if "messages" in changed:
        P.save_messages(messages)

    if highest_id is not None:
        write_offset(highest_id)

    print(f"Logged {len(new_rows)} message(s); {len(command_msgs)} command(s); "
          f"{len(join_queue)} join(s); state changed: {sorted(changed) or 'none'}; "
          f"offset now {highest_id}.")


if __name__ == "__main__":
    main()
