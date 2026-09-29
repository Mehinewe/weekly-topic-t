"""
Shadowing challenge — core logic
================================

Flow (all times GMT/UTC):
  Mon   send_shadowing.py --phase video    posts the weekly shadowing video
  Mon–Thu  members DM their shadowing video to the bot (handled by the poller,
           `log_activity.py`, which calls into this module)
  Thu   send_shadowing.py --phase showcase posts the entries as numbered videos
           (anonymous) and DMs every participant a voting ballot (buttons)
  Sun   send_shadowing.py --phase results  closes voting, announces the winner(s)

Only challenge members (`/join_challenge`) can submit or vote. Votes are private
(DM buttons), one per member per week, changeable until the results phase, and
a member cannot vote for their own video.

State is numeric-id-only and committed back by the workflows:
  shadowing_participants.csv   opt-in roster
  shadowing_entries.csv        one row per (week, user): the video's Telegram file_id
  shadowing_votes.csv          one row per (week, voter): who they voted for
  shadowing_state.json         per-week phase markers + the anonymous numbering

Videos themselves are never downloaded or committed; we re-send Telegram's own
copy by file_id.
"""

import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import participation as P
from automation.shared.storage import atomic_write_text

BASE_DIR = P.BASE_DIR
PARTICIPANTS_FILE = BASE_DIR / "shadowing_participants.csv"
ENTRIES_FILE = BASE_DIR / "shadowing_entries.csv"
VOTES_FILE = BASE_DIR / "shadowing_votes.csv"
STATE_FILE = BASE_DIR / "shadowing_state.json"
MESSAGES_FILE = BASE_DIR / "shadowing_messages.json"

PARTICIPANT_FIELDS = ["user_id", "joined_at"]
ENTRY_FIELDS = ["week_monday", "user_id", "kind", "file_id", "file_unique_id", "submitted_at"]
VOTE_FIELDS = ["week_monday", "voter_id", "target_id", "voted_at"]

COMMANDS = {"join_challenge", "leave_challenge", "vote", "start", "myentry"}
PRACTICE_TYPE = "shadowing"      # activity_log `type`; counts as practice, not as a Video Shark video

DEFAULT_SETTINGS = {"enabled": True, "ranking_size": 1}


# --- settings + messages ---------------------------------------------------

def settings(cfg):
    out = dict(DEFAULT_SETTINGS)
    out.update((cfg or {}).get("shadowing") or {})
    return out


def load_messages():
    return json.loads(MESSAGES_FILE.read_text(encoding="utf-8"))


def msg(messages, key, **values):
    return P.render(messages, key, **values)


# --- state ----------------------------------------------------------------

def load_state():
    if not STATE_FILE.exists():
        return {}
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("shadowing_state.json must be a JSON object; restore it from Git.")
    return state


def save_state(state):
    atomic_write_text(STATE_FILE, json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def week_state(state, week_iso):
    return state.get(week_iso) or {}


def submissions_open(state, week_iso):
    """A shadowing video was posted this week and the showcase has not gone out."""
    ws = week_state(state, week_iso)
    return (bool(ws.get("video_message_id")) and not ws.get("showcase_sent")
            and not ws.get("numbering"))


def voting_open(state, week_iso):
    ws = week_state(state, week_iso)
    return bool(ws.get("showcase_sent")) and not ws.get("results_sent") and bool(ws.get("numbering"))


def voting_week(state):
    """The most recent week whose voting is open (None if none)."""
    for week in sorted(state, reverse=True):
        if voting_open(state, week):
            return week
    return None


def submission_week(state):
    for week in sorted(state, reverse=True):
        if submissions_open(state, week):
            return week
    return None


# --- csv-backed collections ----------------------------------------------

def load_participants():
    return {int(r["user_id"]): r for r in P.read_rows(PARTICIPANTS_FILE) if r.get("user_id")}


def save_participants(participants):
    P.write_rows(PARTICIPANTS_FILE, PARTICIPANT_FIELDS,
                 [participants[u] for u in sorted(participants)])


def load_entries(week_iso):
    return {int(r["user_id"]): r for r in P.read_rows(ENTRIES_FILE)
            if r.get("week_monday") == week_iso and r.get("user_id")}


def save_entry(entry):
    """Insert or replace the (week, user) entry."""
    rows = [r for r in P.read_rows(ENTRIES_FILE)
            if (r.get("week_monday"), r.get("user_id")) != (entry["week_monday"], str(entry["user_id"]))]
    rows.append(entry)
    P.write_rows(ENTRIES_FILE, ENTRY_FIELDS, rows)


def load_votes(week_iso):
    return {int(r["voter_id"]): int(r["target_id"]) for r in P.read_rows(VOTES_FILE)
            if r.get("week_monday") == week_iso and r.get("voter_id") and r.get("target_id")}


def save_vote(week_iso, voter, target):
    rows = [r for r in P.read_rows(VOTES_FILE)
            if (r.get("week_monday"), r.get("voter_id")) != (week_iso, str(voter))]
    rows.append({"week_monday": week_iso, "voter_id": voter, "target_id": target,
                 "voted_at": P.iso_now()})
    P.write_rows(VOTES_FILE, VOTE_FIELDS, rows)


# --- pure rules (unit-tested) -----------------------------------------------

def assign_numbers(user_ids, week_iso):
    """{ "1": uid, ... } in a shuffled but reproducible order, so an entry's
    number reveals nothing about who sent it first."""
    ids = sorted(user_ids)
    random.Random(f"shadowing-{week_iso}").shuffle(ids)
    return {str(i): uid for i, uid in enumerate(ids, start=1)}


def message_kind(message):
    if "video_note" in message:
        return "video_note", message["video_note"]
    if "video" in message:
        return "video", message["video"]
    return None, None


def tally(votes, numbering):
    """[(user_id, vote_count)] for every numbered entry, votes for unknown ids and
    self-votes ignored."""
    valid = set(numbering.values())
    counts = {uid: 0 for uid in valid}
    for voter, target in votes.items():
        if target in valid and target != voter:
            counts[target] += 1
    return list(counts.items())


def rank(counts, entries, size):
    """Top `size` entries: most votes first, ties broken by earliest submission.
    Entries with zero votes never place. Returns [(user_id, votes)]."""
    def key(item):
        uid, votes = item
        return (-votes, (entries.get(uid) or {}).get("submitted_at", ""), uid)
    ordered = [c for c in sorted(counts, key=key) if c[1] > 0]
    return ordered[:max(1, int(size))]


def tie_affects_ranking(counts, ranked):
    """True when someone outside the ranked list has the same votes as the last
    ranked entry (so the tie-break decided a place)."""
    if not ranked:
        return False
    last_votes = ranked[-1][1]
    placed = {uid for uid, _ in ranked}
    return any(v == last_votes and uid not in placed for uid, v in counts)


def ballot_buttons(week_iso, numbering, voter):
    """Inline keyboard rows: one button per entry except the voter's own."""
    buttons = [{"text": f"Video {n}", "callback_data": f"sv:{week_iso}:{n}"}
               for n, uid in sorted(numbering.items(), key=lambda kv: int(kv[0]))
               if uid != voter]
    return [buttons[i:i + 4] for i in range(0, len(buttons), 4)]


def parse_callback(data):
    """'sv:2026-10-05:3' -> ('2026-10-05', '3'); None if it is not ours."""
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != "sv" or P.parse_date(parts[1]) is None or not parts[2].isdigit():
        return None
    return parts[1], parts[2]


def record_practice_activity(sent_dt):
    """activity_log row for a DM'd shadowing video: counts as a practice day."""
    return {
        "iso_time": sent_dt.isoformat(),
        "week_monday": P.monday_of(sent_dt.date()).isoformat(),
        "type": PRACTICE_TYPE,
        "is_reply": 0,
        "reply_to_user_id": "",
        "practice": 1,
    }


# --- Telegram helpers ----------------------------------------------------------

def reply_markup(rows):
    return json.dumps({"inline_keyboard": rows})


def send_ballot(token, voter, week_iso, numbering, messages, current_vote=None):
    """DM a voting ballot. Returns (delivered, reason)."""
    rows = ballot_buttons(week_iso, numbering, voter)
    if not rows:
        return False, "nothing to vote on"
    text = msg(messages, "ballot")
    if current_vote:
        text += "\n\n" + msg(messages, "ballot_current", number=current_vote)
    body = P.tg(token, "sendMessage", http="post", chat_id=voter, text=text,
                parse_mode="HTML", reply_markup=reply_markup(rows))
    if body.get("ok"):
        return True, "ok"
    return False, body.get("description") or "unknown error"


def _number_of(numbering, uid):
    for n, u in numbering.items():
        if u == uid:
            return n
    return None


def register_vote(state, week_iso, voter, number, participants, entries_by_user=None):
    """Validate + store a vote. Returns (ok, key, values) where `key` is a
    message key in shadowing_messages.json."""
    ws = week_state(state, week_iso)
    if not voting_open(state, week_iso):
        return False, "vote_closed", {}
    if voter not in participants:
        return False, "vote_not_member", {}
    target = ws["numbering"].get(str(number))
    if target is None:
        return False, "vote_bad_number", {"count": len(ws["numbering"])}
    if target == voter:
        return False, "vote_own", {}
    save_vote(week_iso, voter, target)
    return True, "vote_ok", {"number": number}


# --- commands (group or DM) ----------------------------------------------

JOIN_PAYLOAD = "join"


def join_link(token):
    """Deep link that opens a private chat with the bot and joins on Start, or None."""
    body = P.tg(token, "getMe")
    username = (body.get("result") or {}).get("username") if body.get("ok") else None
    return f"https://t.me/{username}?start={JOIN_PAYLOAD}" if username else None


def is_group_member(token, chat_id, user_id):
    body = P.tg(token, "getChatMember", chat_id=chat_id, user_id=user_id)
    status = (body.get("result") or {}).get("status") if body.get("ok") else None
    return status in {"member", "administrator", "creator", "restricted"}


def ballot_text(messages, result_text):
    return msg(messages, "ballot") + "\n\n" + result_text


def handle_command(message, token, main_chat_id, cfg, messages, state, today):
    """Handle one shadowing command. Returns (reply_text or None, notes) where
    `notes` is a dict of side effects: {"activity": row, ...}. The caller sends
    the reply to the same chat the command came from."""
    text = (message.get("text") or "").strip()
    head, *rest = text.split(maxsplit=1)
    cmd = head.split("@", 1)[0].lower().lstrip("/")
    args = rest[0].split() if rest else []
    sender = (message.get("from") or {}).get("id")
    if sender is None or cmd not in COMMANDS:
        return None
    is_private = (message.get("chat") or {}).get("type") == "private"
    if cmd == "start" and is_private and args[:1] == [JOIN_PAYLOAD]:
        cmd = "join_challenge"      # tapped the join link: t.me/<bot>?start=join
    participants = load_participants()
    week = P.monday_of(today).isoformat()

    if cmd == "join_challenge":
        if is_private and sender not in participants and not is_group_member(token, main_chat_id, sender):
            return msg(messages, "join_not_in_group")
        if sender not in participants:
            participants[sender] = {"user_id": str(sender), "joined_at": P.iso_now()}
            save_participants(participants)
            key = "join_ok"
        else:
            key = "join_already"
        return msg(messages, key, private_hint="" if is_private else msg(messages, "join_private_hint"))

    if cmd == "leave_challenge":
        if sender in participants:
            del participants[sender]
            save_participants(participants)
            return msg(messages, "leave_ok")
        return msg(messages, "leave_not_in")

    if cmd == "start":
        if not is_private:
            return None
        key = "start_member" if sender in participants else "start_new"
        return msg(messages, key)

    if cmd == "myentry":
        wk = submission_week(state) or voting_week(state) or week
        entry = load_entries(wk).get(sender)
        return msg(messages, "myentry_yes" if entry else "myentry_no")

    if cmd == "vote":
        if not is_private:
            return msg(messages, "vote_dm_only")
        wk = voting_week(state)
        if wk is None:
            return msg(messages, "vote_closed")
        if sender not in participants:
            return msg(messages, "vote_not_member")
        numbering = state[wk]["numbering"]
        if args:
            ok, key, values = register_vote(state, wk, sender, args[0].lstrip("#"), participants)
            return msg(messages, key, **values)
        # No number typed: (re)send the buttons.
        current = load_votes(wk).get(sender)
        ok, _ = send_ballot(token, sender, wk, numbering, messages,
                            _number_of(numbering, current) if current else None)
        return None if ok else msg(messages, "vote_no_ballot")
    return None


def handle_submission(message, cfg, messages, state, today):
    """A video / video note sent to the bot in a private chat.

    Returns (reply_text, activity_row_or_None)."""
    sender = (message.get("from") or {}).get("id")
    kind, media = message_kind(message)
    if sender is None or kind is None:
        return msg(messages, "dm_unknown"), None
    if sender not in load_participants():
        return msg(messages, "submit_not_member"), None
    wk = submission_week(state)
    if wk is None:
        if voting_week(state):
            return msg(messages, "submit_closed"), None
        return msg(messages, "submit_none_open"), None
    sent = datetime.fromtimestamp(message.get("date", 0), tz=timezone.utc)
    previous = load_entries(wk).get(sender)
    save_entry({
        "week_monday": wk, "user_id": sender, "kind": kind,
        "file_id": media.get("file_id", ""), "file_unique_id": media.get("file_unique_id", ""),
        "submitted_at": sent.isoformat(),
    })
    activity = None if previous else record_practice_activity(sent)
    if activity:
        activity["user_id"] = sender
    return msg(messages, "submit_replaced" if previous else "submit_ok"), activity


def handle_callback(callback, cfg, messages, state):
    """A ballot button tap. Returns (ok, text_for_edit, week_iso) or None if the
    callback is not a shadowing vote."""
    parsed = parse_callback(callback.get("data"))
    if parsed is None:
        return None
    week_iso, number = parsed
    voter = (callback.get("from") or {}).get("id")
    if voter is None:
        return None
    ok, key, values = register_vote(state, week_iso, voter, number, load_participants())
    return ok, msg(messages, key, **values), week_iso
