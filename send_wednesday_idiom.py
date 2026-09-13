"""Post a Wednesday idiom picture, then reply with its evening explanation.

Preview: python send_wednesday_idiom.py --phase challenge --date 2026-09-16 --dry-run
State is committed by Actions; no Telegram updates are consumed here.
"""

import argparse
import json
import os
from datetime import date, datetime, time, timezone
from pathlib import Path

import requests

import send_weekly_topic as topic

BASE_DIR = Path(__file__).resolve().parent
SCHEDULE_FILE = BASE_DIR / "idioms_wednesday.json"
IMAGES_DIR = BASE_DIR / "images_idioms"
STATE_FILE = BASE_DIR / "idiom_sent_log.json"
CHALLENGE_TIME = time(10, 32)
REVEAL_TIME = time(18, 32)


def messages(row):
    challenge = (
        "🧩 WEDNESDAY IDIOM CHALLENGE\n\n"
        "Which English idiom does this picture illustrate?\n"
        f"Hint: {row['hint']}\n\n"
        "Send your guess, or describe the picture in a short voice note.\n"
        "💡 Answer + examples at 18:32 UTC today!"
    )
    reveal = (
        f"💡 IDIOM REVEAL: {row['idiom']}\n\n"
        f"Meaning: {row['meaning']}\n\n"
        f"Examples:\n• {row['examples'][0]}\n• {row['examples'][1]}\n\n"
        f"🎤 Your turn: {row['speaking_prompt']}\n"
        "Use today's idiom in a 20–30 second voice note."
    )
    return challenge, reveal


def load_schedule():
    rows = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Idiom schedule must be a nonempty JSON list.")
    dates = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each idiom must be a JSON object.")
        for field in ("date", "image", "idiom", "hint", "meaning", "speaking_prompt"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"Idiom has a missing or empty {field}.")
        day = date.fromisoformat(row["date"])
        if day.weekday() != 2 or day.isoformat() != row["date"]:
            raise ValueError(f"Use a Wednesday YYYY-MM-DD date: {row['date']}")
        if day in dates:
            raise ValueError(f"Duplicate idiom date: {day}")
        dates.add(day)
        examples = row.get("examples")
        if (not isinstance(examples, list) or len(examples) != 2
                or any(not isinstance(e, str) or not e.strip() for e in examples)):
            raise ValueError(f"{day}: supply exactly two example sentences.")
        image_path = (IMAGES_DIR / row["image"]).resolve()
        if (not image_path.is_relative_to(IMAGES_DIR.resolve())
                or image_path.suffix.lower() not in (".png", ".jpg", ".jpeg")
                or not image_path.is_file()):
            raise ValueError(f"{day}: image must exist inside images_idioms/.")
        challenge, reveal = messages(row)
        # Count UTF-16 units conservatively for Telegram's message limits.
        if len(challenge.encode("utf-16-le")) // 2 > 1024:
            raise ValueError(f"{day}: challenge exceeds the photo caption limit.")
        if len(reveal.encode("utf-16-le")) // 2 > 4096:
            raise ValueError(f"{day}: reveal exceeds the text message limit.")
    return rows


def load_state():
    state = json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))
    if not isinstance(state, dict):
        raise ValueError("Idiom state must be a JSON object; restore it from Git.")
    for day, entry in state.items():
        date.fromisoformat(day)
        if (not isinstance(entry, dict)
                or not isinstance(entry.get("challenge_message_id"), int)
                or entry["challenge_message_id"] <= 0
                or not isinstance(entry.get("reveal_text"), str)
                or not entry["reveal_text"].strip()):
            raise ValueError(f"Invalid idiom state for {day}; restore it from Git.")
    return state


def save_state(state):
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(STATE_FILE)


def in_scheduled_window(phase, now):
    now = now.astimezone(timezone.utc)
    if now.weekday() != 2:
        return False
    clock = now.time()
    if phase == "challenge":
        return CHALLENGE_TIME <= clock < REVEAL_TIME
    return clock >= REVEAL_TIME


def send_reveal(token, chat_id, text, message_id):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text,
              "reply_parameters": {"message_id": message_id}},
        timeout=topic.API_TIMEOUT,
    )
    body = topic._check(response, "sendMessage")
    return (body.get("result") or {}).get("message_id")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("challenge", "reveal"), required=True)
    parser.add_argument("--date", type=date.fromisoformat,
                        help="Wednesday YYYY-MM-DD (default: today in UTC)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scheduled", action="store_true",
                        help="Skip delayed jobs outside their Wednesday UTC window")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    if args.scheduled and args.date:
        parser.error("--scheduled cannot be combined with --date")
    if args.scheduled and not in_scheduled_window(args.phase, now):
        print("Outside the scheduled Wednesday window; skipping.")
        return
    target = args.date or now.date()
    if target.weekday() != 2:
        parser.error("Target date must be a Wednesday; use --date for previews.")
    key = target.isoformat()
    state = load_state()
    entry = state.get(key, {})
    if entry.get(f"{args.phase}_message_id") and not args.dry_run:
        print(f"{key} {args.phase} already sent; skipping.")
        return

    # Keep the evening answer paired with the actual morning post, even if
    # someone edits or removes the schedule entry during the day.
    if args.phase == "reveal" and entry:
        text = entry["reveal_text"]
    else:
        row = next((r for r in load_schedule() if r["date"] == key), None)
        if row is None:
            raise ValueError(f"No idiom scheduled for {key}; add one to {SCHEDULE_FILE.name}.")
        challenge, reveal = messages(row)
        text = challenge if args.phase == "challenge" else reveal

    if args.dry_run:
        print(f"--- DRY RUN: {key} {args.phase} (nothing sent or written) ---")
        if args.phase == "challenge":
            print(f"Image: images_idioms/{row['image']}")
        elif not entry:
            print("Preview only: a real reveal requires a recorded morning challenge.")
        else:
            print(f"Reply to challenge message {entry['challenge_message_id']}")
        print(text)
        return

    if args.phase == "reveal" and not entry:
        raise ValueError("Morning challenge was not recorded; refusing an orphan reveal.")
    topic.load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID for real sends.")
    if args.phase == "challenge":
        message_id = topic.send_photo(token, chat_id, IMAGES_DIR / row["image"], text)
    else:
        message_id = send_reveal(token, chat_id, text, entry["challenge_message_id"])
    if not isinstance(message_id, int) or message_id <= 0:
        raise ValueError("Telegram returned no message id; inspect the chat before retrying.")
    if args.phase == "challenge":
        state[key] = {"challenge_message_id": message_id, "reveal_text": reveal,
                      "idiom": row["idiom"], "challenge_sent_at": now.isoformat()}
    else:
        state[key].update(reveal_message_id=message_id, reveal_sent_at=now.isoformat())
    save_state(state)
    # Register replies with the existing participation classifier. Voice/video
    # already count; no second getUpdates poller is introduced.
    try:
        import participation
        participation.record_topic_post(message_id, chat_id, "wednesday_idiom")
    except Exception:
        print("Warning: post sent and saved, but participation topic registration failed.")
    print(f"Done: {key} {args.phase} recorded.")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException:
        # Request exceptions may contain the bot token in the URL.
        topic._fail("Telegram request failed; inspect the chat before retrying.")
    except (OSError, ValueError) as exc:
        topic._fail(str(exc))
