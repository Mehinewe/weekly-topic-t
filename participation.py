"""
Participation & inactivity management — shared core
==================================================

Everything the participation feature needs that is NOT a workflow entry point:
config/message loading, the "does this message count as English practice?"
classifier, the weekly active-day tally, the strike state machine, and thin
Telegram helpers (DM with graceful 403 handling, kick, name lookup).

Used by:
  * log_activity.py            — classifies each message at ingest, tracks the
                                 roster, handles /pause + admin commands
  * participation_commands.py  — the command handlers
  * send_reminders.py          — Wednesday / Friday nudges
  * evaluate_participation.py  — Monday strike evaluation + removals + report

Design notes
------------
* State lives in CSV/JSON committed back to the repo, same as activity_log.csv.
  Everything is keyed by the numeric Telegram user id — NO names are stored, so
  the repo stays safe to be public. Names are resolved live (getChatMember) only
  when a message is actually sent.
* Weeks run Monday 00:00 -> Sunday 23:59:59 in config["week_timezone"] (UTC by
  default). Activity timestamps are stored in UTC and converted on read.
* The classifier runs once, at log time, and writes a `practice` 0/1 column.
  Message text itself is never stored.
"""

import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# --- Paths --------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE       = BASE_DIR / "participation_config.json"
MESSAGES_FILE     = BASE_DIR / "participation_messages.json"
ACTIVITY_LOG      = BASE_DIR / "activity_log.csv"
MEMBERS_FILE      = BASE_DIR / "members.csv"
PAUSE_REQ_FILE    = BASE_DIR / "pause_requests.csv"
REMOVALS_FILE     = BASE_DIR / "removals.csv"
REMINDER_LOG_FILE = BASE_DIR / "reminder_log.csv"
WEEKLY_RESULT_FILE = BASE_DIR / "weekly_results.csv"
EVAL_LOG_FILE     = BASE_DIR / "participation_eval_log.csv"
TOPIC_POSTS_FILE  = BASE_DIR / "topic_posts.csv"

API_TIMEOUT = 30

# --- Field definitions ------------------------------------------------------

ACTIVITY_FIELDS = [
    "iso_time", "week_monday", "user_id", "type", "is_reply",
    "reply_to_user_id", "practice",
]

MEMBER_FIELDS = [
    "user_id", "joined_at", "grace_until", "status",
    "strikes", "strike1_week", "success_streak",
    "pause_start", "pause_end", "pauses_used",
    "last_seen", "last_active_week", "removed_at", "removal_count", "notes",
]

PAUSE_REQ_FIELDS = [
    "iso_time", "user_id", "weeks", "reason", "status",
    "decided_by", "decided_at", "starts", "ends",
]

REMOVAL_FIELDS = ["iso_time", "user_id", "reason", "week_monday", "strikes", "dm_delivered"]
REMINDER_LOG_FIELDS = ["week_monday", "user_id", "which", "iso_time"]
WEEKLY_RESULT_FIELDS = [
    "week_monday", "user_id", "active_days", "passed",
    "strikes_after", "streak_after", "action", "iso_time",
]
EVAL_LOG_FIELDS = ["week_monday", "iso_time"]
TOPIC_POSTS_FIELDS = ["message_id", "chat_id", "iso_time", "kind"]

# Member statuses that the weekly evaluation and the reminders leave alone.
INACTIVE_STATUSES = {"paused", "exempt", "removed", "left"}

# Telegram `message` keys that mark a service event we must not treat as chat.
SERVICE_KEYS = (
    "new_chat_members", "left_chat_member", "new_chat_title", "new_chat_photo",
    "delete_chat_photo", "group_chat_created", "supergroup_chat_created",
    "channel_chat_created", "message_auto_delete_timer_changed", "pinned_message",
    "migrate_to_chat_id", "migrate_from_chat_id", "video_chat_scheduled",
    "video_chat_started", "video_chat_ended", "video_chat_participants_invited",
)


# --- Config defaults (deep-merged under whatever the JSON file provides) ----

DEFAULT_CONFIG = {
    "required_days": 3,
    "clear_weeks": 4,
    "week_timezone": "UTC",
    "auto_removal": False,
    "classifier": {
        "count_voice": True,
        "count_video": True,
        "count_topic_reply": True,
        "count_member_reply": True,
        "count_forwarded": False,
        "reply_min_words": 4,
        "greeting_stoplist": [],
    },
    "reminders": {
        "first":  {"weekday": "wed", "at_most_active_days": 1},
        "second": {"weekday": "fri", "at_most_active_days": 2},
    },
    "pause": {
        "max_weeks": 2,
        "requires_approval": True,
        "max_per_window": 1,
        "window_days": 90,
    },
    "grace_extra_weeks": 0,
    "welcome_new_members": True,
    "dm_fallback_to_group": False,
    "max_removals_per_run": 5,
    "tracking_starts": None,   # YYYY-MM-DD: ignore any week before this Monday

    "admin_user_ids": [],
    "admin_chat_id": None,
    "enabled": {"reminders": True, "evaluation": True},
}


def _deep_merge(base, override):
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    """Read participation_config.json, filled in with DEFAULT_CONFIG."""
    raw = {}
    if CONFIG_FILE.exists():
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except ValueError as exc:
            _fail(f"participation_config.json is not valid JSON: {exc}")
    return _deep_merge(DEFAULT_CONFIG, raw)


def save_config(cfg):
    """Write config back, dropping the injected defaults we can't tell apart is
    fine — we just persist the whole merged dict, pretty-printed."""
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")


def load_messages():
    if not MESSAGES_FILE.exists():
        return {}
    try:
        return json.loads(MESSAGES_FILE.read_text(encoding="utf-8"))
    except ValueError as exc:
        _fail(f"participation_messages.json is not valid JSON: {exc}")


def save_messages(messages):
    MESSAGES_FILE.write_text(json.dumps(messages, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")


class _SafeDict(dict):
    def __missing__(self, key):        # unknown placeholder -> empty, never crash
        return ""


def render(messages, key, **values):
    """Fill a message template. Missing placeholders render as ''."""
    template = messages.get(key) or ""
    try:
        return template.format_map(_SafeDict(values))
    except (ValueError, IndexError):
        return template


# --- Small generic helpers -------------------------------------------------

def load_dotenv():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def monday_of(d):
    """Monday (date) of the week containing date `d`."""
    return d - timedelta(days=d.weekday())


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")


def parse_date(s):
    s = (s or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def today_in_tz(cfg):
    """Return 'today' as a date in the configured week timezone.

    Only a fixed-offset 'UTC' is supported without extra deps; any other value
    falls back to UTC (documented). This keeps the module dependency-free.
    """
    tzname = (cfg.get("week_timezone") or "UTC").strip()
    now = datetime.now(timezone.utc)
    if tzname.upper() != "UTC":
        try:
            from zoneinfo import ZoneInfo
            now = now.astimezone(ZoneInfo(tzname))
        except Exception:
            pass  # stay on UTC
    return now.date()


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- CSV helpers ----------------------------------------------------------

def read_rows(path):
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_rows(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def append_row(path, fieldnames, row):
    new_file = not Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerow(row)


# --- activity_log.csv: migration + tally ---------------------------------

def migrate_activity_log():
    """Add the `practice` column to an older activity_log.csv, once.

    Old rows get practice='' (unknown). The evaluation only looks at weeks from
    after this system is deployed, so unclassified history is harmless.
    """
    if not ACTIVITY_LOG.exists():
        return
    with ACTIVITY_LOG.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return
    header = rows[0]
    if "practice" in header:
        return
    new_rows = [ACTIVITY_FIELDS]
    for r in rows[1:]:
        # pad/truncate to the old 6-column shape, then append an empty practice
        r = (r + [""] * 6)[:6]
        new_rows.append(r + [""])
    with ACTIVITY_LOG.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(new_rows)
    print("Migrated activity_log.csv: added 'practice' column.")


def active_day_counts(week_monday_iso):
    """{user_id(int): number of distinct days with a practice message} for a week."""
    days = defaultdict(set)
    for row in read_rows(ACTIVITY_LOG):
        if row.get("week_monday") != week_monday_iso:
            continue
        if str(row.get("practice")) != "1":
            continue
        uid = row.get("user_id")
        ts = row.get("iso_time") or ""
        if not uid or len(ts) < 10:
            continue
        try:
            days[int(uid)].add(ts[:10])
        except ValueError:
            continue
    return {uid: len(s) for uid, s in days.items()}


def active_days_for(user_id, week_monday_iso):
    return active_day_counts(week_monday_iso).get(int(user_id), 0)


# --- The classifier -----------------------------------------------------

_STICKERISH = ("sticker", "animation", "dice", "game", "poll", "contact",
               "location", "venue", "photo", "document", "audio")

_EMOJI_PUNCT_RE = re.compile(
    r"[^\w\s]",  # keep letters/digits/underscore/space; drop punctuation & emoji
    flags=re.UNICODE,
)


def _normalise(text):
    """Lowercase, strip punctuation/emoji, collapse whitespace — for the stoplist."""
    t = _EMOJI_PUNCT_RE.sub(" ", (text or "").lower())
    t = re.sub(r"[\d_]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _word_count(text):
    return len(re.findall(r"[^\W\d_]+", text or "", flags=re.UNICODE))


def _is_forwarded(message):
    return any(k in message for k in ("forward_origin", "forward_date",
                                      "forward_from", "forward_from_chat"))


def classify_practice(message, cfg, topic_message_ids):
    """Does this Telegram `message` count as an active English-practice message?

    Strict v1 rule (config-tunable): only voice, video/video_note, a reply to the
    weekly topic, or a genuine reply to another member counts. Standalone text
    never counts, regardless of length.

    Returns (practice: bool, reason: str) — the reason is for --dry-run / debug.
    """
    c = cfg["classifier"]
    stop = {s.lower() for s in c.get("greeting_stoplist", [])}
    min_words = int(c.get("reply_min_words", 4))

    if _is_forwarded(message) and not c.get("count_forwarded", False):
        return False, "forwarded"

    if "voice" in message:
        return (True, "voice") if c.get("count_voice", True) else (False, "voice (disabled)")

    if "video" in message or "video_note" in message:
        return (True, "video") if c.get("count_video", True) else (False, "video (disabled)")

    text = message.get("text")
    if text is not None:
        if text.lstrip().startswith("/"):
            return False, "bot command"

        norm = _normalise(text)
        reply = message.get("reply_to_message") or {}

        if not reply:
            return False, "standalone text (never counts)"

        wc = _word_count(text)
        if not norm or norm in stop:
            return False, "greeting / filler"
        if wc < min_words:
            return False, f"reply too short ({wc} < {min_words} words)"

        rid = reply.get("message_id")
        if rid is not None and str(rid) in {str(x) for x in topic_message_ids}:
            if c.get("count_topic_reply", True):
                return True, "reply to weekly topic"
            return False, "topic reply (disabled)"

        r_from = reply.get("from") or {}
        if r_from.get("is_bot"):
            return False, "reply to the bot"
        r_uid = r_from.get("id")
        s_uid = (message.get("from") or {}).get("id")
        if r_uid in (None, s_uid):
            return False, "reply to self"
        if c.get("count_member_reply", True):
            return True, "reply to another member"
        return False, "member reply (disabled)"

    for k in _STICKERISH:
        if k in message:
            return False, f"{k} only"

    return False, "not a practice message"


# --- The strike state machine ------------------------------------------

def blank_member(user_id, joined_iso, grace_until_iso):
    return {
        "user_id": str(user_id),
        "joined_at": joined_iso,
        "grace_until": grace_until_iso,
        "status": "active",
        "strikes": "0",
        "strike1_week": "",
        "success_streak": "0",
        "pause_start": "",
        "pause_end": "",
        "pauses_used": "0",
        "last_seen": "",
        "last_active_week": "",
        "removed_at": "",
        "removal_count": "0",
        "notes": "",
    }


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


def member_is_trackable(member, week_monday):
    """True if this member should be evaluated / reminded for `week_monday`.

    Skips removed/left/exempt members, members whose pause overlaps the week,
    and members still inside their new-joiner grace period.
    """
    status = (member.get("status") or "active").strip()
    if status in ("removed", "left", "exempt"):
        return False

    grace_until = parse_date(member.get("grace_until"))
    if grace_until and week_monday < grace_until:
        return False

    if status == "paused" or member.get("pause_end"):
        p_start = parse_date(member.get("pause_start"))
        p_end = parse_date(member.get("pause_end"))
        week_end = week_monday + timedelta(days=6)
        if p_start and p_end and p_start <= week_end and p_end >= week_monday:
            return False

    return True


# --- members.csv -------------------------------------------------------

def load_members():
    """{user_id(int): row dict} from members.csv."""
    out = {}
    for row in read_rows(MEMBERS_FILE):
        uid = _int(row.get("user_id"), None)
        if uid is None:
            continue
        # make sure every expected key exists
        for k in MEMBER_FIELDS:
            row.setdefault(k, "")
        out[uid] = row
    return out


def save_members(members):
    rows = [members[uid] for uid in sorted(members)]
    write_rows(MEMBERS_FILE, MEMBER_FIELDS, rows)


def seed_members_from_activity(cfg):
    """Build members.csv from everyone who already appears in activity_log.csv.

    Back-filled members get a grace_until in the past, so they are tracked from
    the next evaluation onward.
    """
    first_seen = {}
    for row in read_rows(ACTIVITY_LOG):
        uid = _int(row.get("user_id"), None)
        ts = row.get("iso_time") or ""
        if uid is None or len(ts) < 10:
            continue
        d = ts[:10]
        if uid not in first_seen or d < first_seen[uid]:
            first_seen[uid] = d
    members = {}
    for uid, d in first_seen.items():
        m = blank_member(uid, d, "2000-01-01")
        m["last_seen"] = d
        members[uid] = m
    print(f"Seeded members.csv from activity_log.csv: {len(members)} member(s).")
    return members


def grace_until_for(join_date, cfg):
    """First Monday from which a new joiner is tracked (rest of the join week is
    grace, plus `grace_extra_weeks` full weeks)."""
    extra = _int(cfg.get("grace_extra_weeks", 0), 0)
    return (monday_of(join_date) + timedelta(days=7 * (1 + extra))).isoformat()


# --- Telegram -----------------------------------------------------------

def tg(token, method, http="get", **params):
    """Call a Bot API method. Returns the parsed body dict (never raises for a
    Telegram-level error — callers inspect body['ok'])."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        if http == "post":
            resp = requests.post(url, data=params, timeout=API_TIMEOUT)
        else:
            resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "description": f"{type(exc).__name__}: {exc}"}


def send_chat(token, chat_id, text, parse_mode="HTML", disable_preview=True):
    """Send a message to a group/channel/admin chat. Returns body dict."""
    body = tg(token, "sendMessage", http="post", chat_id=chat_id, text=text,
              parse_mode=parse_mode, disable_web_page_preview=disable_preview)
    if not body.get("ok"):
        print(f"  sendMessage to {chat_id} failed: {body.get('description')}",
              file=sys.stderr)
    return body


def send_dm(token, user_id, text, parse_mode="HTML"):
    """DM a user. Returns (delivered: bool, reason: str).

    A user who has never opened a chat with the bot cannot be messaged first —
    that comes back as 403 and is reported as delivered=False, not an error.
    """
    body = tg(token, "sendMessage", http="post", chat_id=user_id, text=text,
              parse_mode=parse_mode, disable_web_page_preview=True)
    if body.get("ok"):
        return True, "ok"
    desc = (body.get("description") or "").lower()
    if "429" in desc or "too many requests" in desc:
        retry = ((body.get("parameters") or {}).get("retry_after")) or 3
        time.sleep(min(int(retry) + 1, 30))
        body = tg(token, "sendMessage", http="post", chat_id=user_id, text=text,
                  parse_mode=parse_mode, disable_web_page_preview=True)
        if body.get("ok"):
            return True, "ok (after retry)"
        desc = (body.get("description") or "").lower()
    if "forbidden" in desc or "can't initiate" in desc or "blocked" in desc:
        return False, "no private chat with the bot"
    return False, body.get("description") or "unknown error"


def kick_member(token, chat_id, user_id):
    """Remove a user but let them rejoin later (ban then immediately unban).

    Returns (ok: bool, detail: str). Needs the bot to be an admin with the
    'ban users' right; it cannot remove other admins or the owner.
    """
    ban = tg(token, "banChatMember", http="post", chat_id=chat_id, user_id=user_id)
    if not ban.get("ok"):
        return False, ban.get("description") or "banChatMember failed"
    tg(token, "unbanChatMember", http="post", chat_id=chat_id, user_id=user_id,
       only_if_banned=True)
    return True, "kicked (ban+unban)"


_NAME_CACHE = {}


def resolve_name(token, chat_id, user_id):
    """First name (or @username, or 'User <id>') via getChatMember, cached."""
    uid = int(user_id)
    if uid in _NAME_CACHE:
        return _NAME_CACHE[uid]
    name = f"User {uid}"
    body = tg(token, "getChatMember", chat_id=chat_id, user_id=uid)
    if body.get("ok"):
        user = (body.get("result") or {}).get("user") or {}
        name = (user.get("first_name") or "").strip() or \
               (f"@{user['username']}" if user.get("username") else name)
    _NAME_CACHE[uid] = name
    return name


def mention_html(user_id, name):
    import html
    return f'<a href="tg://user?id={user_id}">{html.escape(name, quote=False)}</a>'


def get_admin_ids(token, chat_id):
    """Set of user ids that are admins/owner of the group (empty on failure)."""
    body = tg(token, "getChatAdministrators", chat_id=chat_id)
    if not body.get("ok"):
        return set()
    return {a["user"]["id"] for a in body.get("result", []) if a.get("user")}


def admin_chat_id(cfg, fallback_chat_id):
    """Where reports / pause approvals go. Falls back to the main group.

    Priority: TELEGRAM_ADMIN_CHAT_ID env var (repo secret) > config value >
    the main group. Kept out of the committed config so a public repo does
    not expose the private admin group's id.
    """
    return (os.environ.get("TELEGRAM_ADMIN_CHAT_ID")
            or cfg.get("admin_chat_id")
            or fallback_chat_id)


# --- topic_posts.csv --------------------------------------------------

def load_topic_message_ids():
    return {row.get("message_id") for row in read_rows(TOPIC_POSTS_FILE)
            if row.get("message_id")}


def record_topic_post(message_id, chat_id, kind="topic"):
    if message_id is None:
        return
    append_row(TOPIC_POSTS_FILE, TOPIC_POSTS_FIELDS, {
        "message_id": message_id,
        "chat_id": chat_id,
        "iso_time": iso_now(),
        "kind": kind,
    })


# --- tiny self-test (python participation.py) -------------------------

if __name__ == "__main__":
    cfg = load_config()
    tids = {"111"}
    samples = [
        ({"voice": {}}, True),
        ({"video_note": {}}, True),
        ({"text": "hi"}, False),
        ({"text": "Thanks everyone, see you tomorrow!"}, False),   # standalone text
        ({"text": "I agree", "reply_to_message": {"message_id": 5,
          "from": {"id": 2}}}, False),                             # too short
        ({"text": "I think the best answer is planning ahead and practising daily",
          "reply_to_message": {"message_id": 5, "from": {"id": 2}}}, True),
        ({"text": "Great question — here is my full answer to the weekly topic today",
          "reply_to_message": {"message_id": 111, "from": {"id": 999, "is_bot": True}}}, True),
        ({"text": "/pause 2"}, False),
        ({"sticker": {}}, False),
        ({"voice": {}, "forward_date": 123}, False),
    ]
    ok = 0
    for msg, expected in samples:
        msg.setdefault("from", {"id": 1})
        got, reason = classify_practice(msg, cfg, tids)
        flag = "ok " if got == expected else "FAIL"
        ok += got == expected
        print(f"  [{flag}] expected={expected!s:5} got={got!s:5} ({reason})")
    print(f"{ok}/{len(samples)} classifier checks passed.")
