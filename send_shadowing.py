"""Shadowing challenge — scheduled posts (all times GMT).

    python send_shadowing.py --phase video    [--date YYYY-MM-DD] [--dry-run]   Monday
    python send_shadowing.py --phase showcase [--date YYYY-MM-DD] [--dry-run]   Thursday
    python send_shadowing.py --phase results  [--date YYYY-MM-DD] [--dry-run]   Sunday

`--date` is any day of the target week. `--scheduled` (used by the workflow)
skips a delayed run that lands outside its weekday. Every step is recorded in
shadowing_state.json as soon as it succeeds, so a re-run resumes instead of
posting twice. Members' entries and votes arrive through log_activity.py (the
single getUpdates poller); nothing here reads Telegram updates.
"""

import argparse
import csv
import tempfile
import html
import os
import sys
import time as _time
from datetime import date, datetime, time, timezone
from pathlib import Path

import requests

import participation as P
import shadowing as S
from automation.shared import telegram as tgshared
from automation.shared.delivery import TelegramRejected
from automation.shared.settings import load_dotenv

BASE_DIR = P.BASE_DIR
SCHEDULE_FILE = BASE_DIR / "schedule_shadowing.csv"
VIDEOS_DIR = BASE_DIR / "videos_shadowing"
BADGES_DIR = BASE_DIR / "badges"
WINNER_BADGE = "shadowing_champion.png"
WINNER_BADGE_TYPE = "shadow"
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm")
CAPTION_LIMIT = 1024
UPLOAD_LIMIT = 50 * 1024 * 1024        # Bot API upload cap for sendVideo
SEND_PAUSE = 3                          # seconds between group posts (group rate limit)

# Weekday (Mon=0) and earliest GMT time each phase may run.
PHASE_WINDOWS = {
    "video": (0, time(10, 47)),
    "showcase": (3, time(15, 47)),
    "results": (6, time(15, 47)),
}


# --- schedule ---------------------------------------------------------------

def load_schedule():
    if not SCHEDULE_FILE.exists():
        return []
    rows, weeks = [], set()
    with SCHEDULE_FILE.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not {"date", "video", "message"}.issubset(reader.fieldnames or []):
            raise ValueError("schedule_shadowing.csv must have columns: date,video,message")
        for raw in reader:
            when = P.parse_date(raw.get("date"))
            video = (raw.get("video") or "").strip()
            message = (raw.get("message") or "").strip()
            if not (when or video or message):
                continue
            if when is None or not video or not message:
                raise ValueError(f"Bad shadowing schedule row: {raw.get('date')!r}")
            monday = P.monday_of(when)
            if monday in weeks:
                raise ValueError(f"Duplicate shadowing week: {monday}")
            weeks.add(monday)
            if not video.startswith("file_id:") and resolve_video(video) is None:
                raise ValueError(f"{monday}: video not found in {VIDEOS_DIR.name}/: {video}")
            rows.append({"monday": monday, "video": video, "message": message})
    return rows


def resolve_video(name):
    path = (VIDEOS_DIR / name).resolve()
    if (path.is_relative_to(VIDEOS_DIR.resolve()) and path.suffix.lower() in VIDEO_EXTS
            and path.is_file()):
        return path
    return None


# --- Telegram calls (checked, never auto-retried) ---------------------------------

def _post(token, method, **fields):
    files = fields.pop("_files", None)
    try:
        resp = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                             data=fields, files=files, timeout=60)
    except requests.RequestException:
        # The exception text can contain the bot token in the URL.
        raise ValueError(f"{method}: request outcome unknown; inspect the chat before retrying.") from None
    return tgshared._check(resp, method)


def send_text(token, chat_id, text):
    body = _post(token, "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                 disable_web_page_preview=True)
    return (body.get("result") or {}).get("message_id")


def send_video_file(token, chat_id, video, caption, button_url=None):
    """Upload a repo video (or reuse a `file_id:` value) with a caption and an
    optional "Join the challenge" button."""
    long_caption = len(caption) > CAPTION_LIMIT
    fields = {"chat_id": chat_id, "caption": caption[:CAPTION_LIMIT] if long_caption else caption,
              "supports_streaming": "true"}
    if button_url:
        fields["reply_markup"] = S.reply_markup(
            [[{"text": "🎤 Join the challenge", "url": button_url}]])
    if video.startswith("file_id:"):
        body = _post(token, "sendVideo", video=video[len("file_id:"):], **fields)
    else:
        path = resolve_video(video)
        if path.stat().st_size > UPLOAD_LIMIT:
            raise ValueError(f"{video} is over 50 MB; compress it or use a file_id: value.")
        with path.open("rb") as fh:
            body = _post(token, "sendVideo", _files={"video": fh}, **fields)
    message_id = (body.get("result") or {}).get("message_id")
    if long_caption:
        send_text(token, chat_id, caption[CAPTION_LIMIT:])
    return message_id


def send_entry(token, chat_id, entry, number):
    label = f"🎬 <b>Video {number}</b>"
    if entry["kind"] == "video_note":
        send_text(token, chat_id, label)
        body = _post(token, "sendVideoNote", chat_id=chat_id, video_note=entry["file_id"])
    else:
        body = _post(token, "sendVideo", chat_id=chat_id, video=entry["file_id"],
                     caption=label, parse_mode="HTML")
    return (body.get("result") or {}).get("message_id")


def name_of(token, chat_id, uid):
    return P.resolve_name(token, chat_id, uid)


def mention(token, chat_id, uid):
    return P.mention_html(uid, name_of(token, chat_id, uid))


# --- phases -------------------------------------------------------------------------

def phase_video(target, state, cfg, messages, token, chat_id, dry_run, force):
    week = target.isoformat()
    ws = state.setdefault(week, {})
    if ws.get("video_message_id") and not force:
        print(f"{week} shadowing video already posted; skipping.")
        return False
    row = next((r for r in load_schedule() if r["monday"] == target), None)
    if row is None:
        raise ValueError(f"No shadowing video scheduled for {week}; add a row to {SCHEDULE_FILE.name}.")
    if dry_run:
        print(f"--- DRY RUN: {week} shadowing video (nothing sent or written) ---")
        print(f"Video: {row['video']}")
        print(row["message"])
        return False
    message_id = send_video_file(token, chat_id, row["video"], row["message"],
                                 S.join_link(token))
    if type(message_id) is not int or message_id <= 0:
        raise ValueError("Telegram returned no message id; inspect the chat before retrying.")
    ws.update(video_message_id=message_id, video_sent_at=P.iso_now())
    S.save_state(state)
    try:
        P.record_topic_post(message_id, chat_id, "shadowing_video")
    except Exception:
        print("Warning: post saved, but participation topic registration failed.")
    print(f"Done: {week} shadowing video recorded.")
    return True


def phase_showcase(target, state, cfg, messages, token, chat_id, dry_run, force):
    week = target.isoformat()
    ws = state.get(week) or {}
    if not ws.get("video_message_id"):
        print(f"No shadowing video was posted for {week}; nothing to showcase.")
        return False
    if ws.get("showcase_sent"):
        print(f"{week} showcase already sent; skipping.")
        return False
    entries = S.load_entries(week)
    numbering = ws.get("numbering") or S.assign_numbers(entries, week)
    count = len(entries)

    if dry_run:
        print(f"--- DRY RUN: {week} showcase (nothing sent or written) ---")
        print(f"{count} entr{'y' if count == 1 else 'ies'}; participants: {len(S.load_participants())}")
        if count >= 2:
            for n, uid in sorted(numbering.items(), key=lambda kv: int(kv[0])):
                print(f"  Video {n}: user {uid} ({entries[uid]['kind']})")
        return False

    ws = state.setdefault(week, ws)
    if count < 2:
        text = S.msg(messages, "showcase_none" if count == 0 else "showcase_one")
        send_text(token, chat_id, text)
        if count == 1:
            uid = next(iter(entries))
            send_entry(token, chat_id, entries[uid], 1)
        ws.update(showcase_sent=True, showcase_sent_at=P.iso_now(), entry_count=count)
        S.save_state(state)
        return True

    if not ws.get("numbering"):
        # Lock entries + numbering BEFORE posting anything, so a retry reuses them.
        ws.update(numbering=numbering, entry_count=count)
        S.save_state(state)
    if not ws.get("intro_sent"):
        send_text(token, chat_id, S.msg(messages, "showcase_intro", count=count))
        ws["intro_sent"] = True
        S.save_state(state)
    sent_numbers = ws.setdefault("entries_sent", [])
    for n, uid in sorted(numbering.items(), key=lambda kv: int(kv[0])):
        if n in sent_numbers:
            continue
        send_entry(token, chat_id, entries[int(uid)], n)
        sent_numbers.append(n)
        S.save_state(state)
        _time.sleep(SEND_PAUSE)

    ballots = ws.setdefault("ballots_sent", [])
    failed = ws.setdefault("ballots_failed", [])
    for voter in sorted(S.load_participants()):
        if voter in ballots or voter in failed:
            continue
        ok, reason = S.send_ballot(token, voter, week, numbering, messages)
        (ballots if ok else failed).append(voter)
        S.save_state(state)
        _time.sleep(1)
    if failed and not ws.get("missing_notice_sent"):
        names = ", ".join(mention(token, chat_id, uid) for uid in failed)
        send_text(token, chat_id, S.msg(messages, "showcase_dm_missing", names=names).strip())
        ws["missing_notice_sent"] = True
    ws.update(showcase_sent=True, showcase_sent_at=P.iso_now())
    S.save_state(state)
    print(f"Done: {week} showcase — {count} entries, {len(ballots)} ballots, {len(failed)} undeliverable.")
    return True


def phase_results(target, state, cfg, messages, token, chat_id, dry_run, force):
    week = target.isoformat()
    ws = state.get(week) or {}
    if ws.get("results_sent"):
        print(f"{week} results already sent; skipping.")
        return False
    if not ws.get("showcase_sent") or not ws.get("numbering"):
        print(f"{week}: no vote took place (fewer than 2 entries or no showcase); nothing to announce.")
        return False
    entries = S.load_entries(week)
    votes = S.load_votes(week)
    counts = S.tally(votes, ws["numbering"])
    size = int(S.settings(cfg)["ranking_size"])
    ranked = S.rank(counts, entries, size)

    if dry_run:
        print(f"--- DRY RUN: {week} results (nothing sent or written) ---")
        print(f"{len(votes)} vote(s); ranking_size={size}")
        for place, (uid, n) in enumerate(ranked, start=1):
            print(f"  #{place}: user {uid} with {n} vote(s)")
        if S.tie_affects_ranking(counts, ranked):
            print("  (tie-break by earliest submission applied)")
        return False

    ws = state.setdefault(week, ws)
    if not ranked:
        send_text(token, chat_id, S.msg(messages, "results_none"))
        ws.update(results_sent=True, results_sent_at=P.iso_now())
        S.save_state(state)
        return True

    if not ws.get("results_text_sent"):
        medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 20
        lines = [S.msg(messages, "results_header", week=week), ""]
        for place, (uid, n) in enumerate(ranked):
            key = "results_winner" if place == 0 else "results_place"
            lines.append(S.msg(messages, key, medal=medals[place],
                               name=mention(token, chat_id, uid), votes=n))
        if S.tie_affects_ranking(counts, ranked):
            lines.append(S.msg(messages, "results_tiebreak"))
        lines += ["", S.msg(messages, "results_footer")]
        names = [(name_of(token, chat_id, uid), n) for uid, n in ranked]
        send_results(token, chat_id, "\n".join(lines), names, f"Week of {week}")
        ws["results_text_sent"] = True
        S.save_state(state)

    winner = ranked[0][0]
    if not ws.get("winner_video_sent") and winner in entries:
        _time.sleep(SEND_PAUSE)
        send_entry(token, chat_id, entries[winner], ws_number(ws, winner))
        ws["winner_video_sent"] = True
        S.save_state(state)

    if not ws.get("winner_badge_sent"):
        try:
            post_winner_badge(token, chat_id, winner, messages)
        except Exception as exc:  # noqa: BLE001 - the results are already out; never fail the run over a badge
            print(f"Warning: winner badge not posted: {exc}", file=sys.stderr)
        ws["winner_badge_sent"] = True
        S.save_state(state)

    ws.update(results_sent=True, results_sent_at=P.iso_now(), winners=[u for u, _ in ranked])
    S.save_state(state)
    print(f"Done: {week} results announced.")
    return True


def send_results(token, chat_id, caption, names, subtitle):
    """Results as a podium picture with the mentions as its caption. If the picture
    can't be built (e.g. Pillow missing) the same caption goes out as plain text."""
    try:
        from make_podium import build_podium
        with tempfile.TemporaryDirectory() as tmp:
            image = build_podium(names, subtitle, Path(tmp) / "podium.png")
            with open(image, "rb") as fh:
                _post(token, "sendPhoto", chat_id=chat_id, caption=caption,
                      parse_mode="HTML", _files={"photo": fh})
    except (ImportError, OSError) as exc:
        print(f"  (podium image failed: {exc}; sending text)", file=sys.stderr)
        send_text(token, chat_id, caption)


def ws_number(ws, uid):
    for n, u in ws["numbering"].items():
        if u == uid:
            return n
    return "?"


def post_winner_badge(token, chat_id, winner, messages):
    """Same look as the weekly awards: badge (flip GIF with their photo when they have one)
    plus a button to a badge avatar. Reuses the awards module's helpers."""
    import send_weekly_awards as A
    media = A.resolve_media(WINNER_BADGE)
    if media is None:
        print("No badges/shadowing_champion.png found — skipping the winner badge.")
        return
    first = A.resolve_name(token, chat_id, winner)
    caption = html.escape(messages["winner_badge"], quote=False).replace(
        "{name}", A.mention_html(winner, first))
    app_url = (os.environ.get("BADGE_APP_URL") or A.DEFAULT_BADGE_APP_URL).rstrip("/")
    button_url = f"{app_url}/?type={WINNER_BADGE_TYPE}"
    send_path, temp_gif, temp_avatar = media, None, None
    photo = A.get_profile_photo(token, winner) if media.suffix.lower() in A.IMAGE_EXTS else None
    if photo is not None:
        try:
            from make_award_gif import build_flip_gif, build_badge_avatar
            temp_gif = BADGES_DIR / "_flip_shadowing.gif"
            build_flip_gif(media, photo, temp_gif)
            send_path = temp_gif
            temp_avatar = BADGES_DIR / "_avatar_shadowing.png"
            build_badge_avatar(media, photo, temp_avatar)
            hosted = A.upload_avatar(temp_avatar, "shadowing")
            if hosted:
                button_url = hosted
        except Exception as exc:  # noqa: BLE001
            print(f"  (personalisation failed: {exc}; sending the static badge)", file=sys.stderr)
    try:
        if len(caption) <= A.CAPTION_LIMIT:
            A.send_badge(token, chat_id, send_path, caption, button_url)
        else:
            A.send_badge(token, chat_id, send_path, caption[:A.CAPTION_LIMIT], button_url)
            A.send_message(token, chat_id, caption[A.CAPTION_LIMIT:])
    finally:
        for tmp in (temp_gif, temp_avatar):
            if tmp is not None and tmp.exists():
                tmp.unlink()


PHASES = {"video": phase_video, "showcase": phase_showcase, "results": phase_results}


# --- entry point ---------------------------------------------------------------------

def in_scheduled_window(phase, now):
    weekday, earliest = PHASE_WINDOWS[phase]
    now = now.astimezone(timezone.utc)
    return now.weekday() == weekday and now.time() >= earliest


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=sorted(PHASES))
    parser.add_argument("--join-link", action="store_true",
                        help="Print the t.me link members tap to join the challenge")
    parser.add_argument("--date", type=date.fromisoformat,
                        help="Any date in the target week (default: today, GMT)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scheduled", action="store_true",
                        help="Skip delayed jobs that land outside their weekday")
    parser.add_argument("--force", action="store_true",
                        help="Re-post the Monday video even if this week is recorded")
    args = parser.parse_args(argv)
    if args.join_link:
        load_dotenv()
        link = S.join_link(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
        print(link or "Could not look up the bot username; check TELEGRAM_BOT_TOKEN.")
        return
    if not args.phase:
        parser.error("--phase is required (or use --join-link)")
    now = datetime.now(timezone.utc)
    if args.scheduled and args.date:
        parser.error("--scheduled cannot be combined with --date")
    if args.scheduled and not in_scheduled_window(args.phase, now):
        print("Outside the scheduled GMT window; skipping.")
        return
    load_dotenv()
    cfg = P.load_config()
    if not S.settings(cfg)["enabled"]:
        print("Shadowing challenge is disabled (shadowing.enabled = false); skipping.")
        return
    target = P.monday_of(args.date or now.date())
    messages = S.load_messages()
    state = S.load_state()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not args.dry_run and (not token or not chat_id):
        raise ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID for real sends.")
    PHASES[args.phase](target, state, cfg, messages, token, chat_id, args.dry_run, args.force)


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException:
        tgshared._fail("Telegram request failed; inspect the chat before retrying.")
    except (OSError, ValueError, TelegramRejected) as exc:
        tgshared._fail(str(exc))
