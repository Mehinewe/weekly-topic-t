"""
Weekly Telegram Topic Auto-Poster
=================================

Posts this week's English topic (a PNG image + a message) to your Telegram group.

It reads `schedule.csv`, picks the row for the current week (this Monday), and
sends the matching image from `images/` with the message as the caption.

Run manually for testing:
    python send_weekly_topic.py            # picks this week's row
    python send_weekly_topic.py 2026-06-22 # force a specific Monday (for testing)

By default it reads schedule.csv + images/. Point it at a different content set
with --schedule and --images (used by the Wednesday poster):
    python send_weekly_topic.py --schedule schedule_wednesday.csv --images images_wednesday

Required environment variables:
    TELEGRAM_BOT_TOKEN   the token from @BotFather
    TELEGRAM_CHAT_ID     your group's chat id (negative number, e.g. -1001234567890)
"""

import csv
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

# Print UTF-8 so emoji captions don't crash the Windows console (cp1252).
# This only affects console output; sending to Telegram is always UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# --- Configuration --------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
SCHEDULE_FILE = BASE_DIR / "schedule.csv"
IMAGES_DIR = BASE_DIR / "images"

# Records which (schedule, week) posts have already gone out, so a run that
# repeats — a delayed GitHub cron, a backup cron time, a manual re-trigger —
# can see the week is done and skip. Like activity_log.csv, this is committed
# back to the repo by the workflow so state survives across stateless runs, so
# it is intentionally NOT gitignored.
SENT_LOG_FILE = BASE_DIR / "sent_log.csv"

# Telegram limits a photo caption to 1024 characters. Longer messages are
# split: the photo goes out with the first chunk, the rest as a text message.
CAPTION_LIMIT = 1024

API_TIMEOUT = 30  # seconds


# --- Helpers --------------------------------------------------------------

def load_dotenv():
    """
    Load KEY=VALUE pairs from a local .env file into the environment, if present.

    Used for local runs. On GitHub Actions there is no .env; the secrets come
    from the environment instead. Existing environment variables are not
    overwritten.
    """
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
    """Print an error and exit non-zero so the scheduler flags the run."""
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def monday_of(d):
    """Return the Monday of the week containing date `d`."""
    return d - timedelta(days=d.weekday())


# Accept both ISO (2026-06-22) and the US/Excel style (6/22/2026).
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")


def _parse_date(date_str):
    """Parse a date string in any supported format, or return None."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def resolve_image(name):
    """
    Find the image file for `name` in images/.

    If the exact filename isn't there, try swapping the extension (so a CSV that
    says 0.png still matches an actual 0.jpg, and vice versa).
    """
    exact = IMAGES_DIR / name
    if exact.exists():
        return exact
    stem = Path(name).stem
    for candidate in sorted(IMAGES_DIR.glob(stem + ".*")):
        if candidate.suffix.lower() in (".png", ".jpg", ".jpeg"):
            return candidate
    return None


def load_schedule():
    """Read schedule.csv into a list of {date, image, message} dicts."""
    if not SCHEDULE_FILE.exists():
        _fail(f"schedule file not found: {SCHEDULE_FILE}")

    rows = []
    with SCHEDULE_FILE.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"date", "image", "message"}
        if not required.issubset(set(reader.fieldnames or [])):
            _fail(
                "schedule.csv must have columns: date, image, message "
                f"(found: {reader.fieldnames})"
            )
        for line_no, raw in enumerate(reader, start=2):
            date_str = (raw.get("date") or "").strip()
            if not date_str:
                continue  # skip blank lines
            parsed = _parse_date(date_str)
            if parsed is None:
                _fail(
                    f"bad date '{date_str}' on line {line_no}; "
                    "use YYYY-MM-DD or M/D/YYYY"
                )
            rows.append(
                {
                    "date": parsed,
                    "image": (raw.get("image") or "").strip(),
                    "message": (raw.get("message") or "").strip(),
                }
            )
    if not rows:
        _fail("schedule.csv has no usable rows")
    return rows


def pick_row(rows, target_monday):
    """
    Choose the row for `target_monday`.

    Prefer an exact match on the Monday date. If none, fall back to the most
    recent past row, so a missed week still re-sends the latest topic rather
    than nothing.
    """
    # Match on the Monday of each row's week, so dates don't have to be exact
    # Mondays (e.g. 6/18 counts as the week starting Monday 6/15).
    exact = [r for r in rows if monday_of(r["date"]) == target_monday]
    if exact:
        return exact[-1]

    past = sorted(
        [r for r in rows if monday_of(r["date"]) <= target_monday],
        key=lambda r: r["date"],
    )
    if past:
        print(
            f"No row for week of {target_monday}; "
            f"falling back to most recent: {past[-1]['date']}"
        )
        return past[-1]

    _fail(f"no schedule row on or before week of {target_monday}")


def send_photo(token, chat_id, image_path, caption):
    """Send a photo with caption via sendPhoto; return the new message_id."""
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    with image_path.open("rb") as photo:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": photo},
            timeout=API_TIMEOUT,
        )
    body = _check(resp, "sendPhoto")
    return (body.get("result") or {}).get("message_id")


def pin_message(token, chat_id, message_id):
    """Pin a message silently (no 'pinned a message' notification to members).

    Requires the bot to be an admin with the 'Pin Messages' permission. A new
    pin replaces the previous one at the top of the chat.
    """
    url = f"https://api.telegram.org/bot{token}/pinChatMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "message_id": message_id,
            "disable_notification": True,
        },
        timeout=API_TIMEOUT,
    )
    _check(resp, "pinChatMessage")


def send_message(token, chat_id, text):
    """Send a plain text message via Telegram sendMessage."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": chat_id, "text": text},
        timeout=API_TIMEOUT,
    )
    _check(resp, "sendMessage")


def _check(resp, what):
    """Validate a Telegram API response, failing loudly on errors."""
    try:
        body = resp.json()
    except ValueError:
        _fail(f"{what}: non-JSON response (HTTP {resp.status_code}): {resp.text[:300]}")
    if not body.get("ok"):
        _fail(f"{what} failed: {body.get('description', resp.text[:300])}")
    print(f"{what} OK")
    return body


# --- Idempotency guard ----------------------------------------------------
# The whole point: GitHub cron is best-effort — a run can be delayed by hours
# or fire more than once, and we add backup cron times on purpose. Recording
# each successful post (keyed by schedule file + the week's Monday) lets every
# later run for the same week no-op instead of double-posting.

def already_sent(schedule_path, target_monday):
    """True if this (schedule, week) was already posted, per sent_log.csv."""
    if not SENT_LOG_FILE.exists():
        return False
    want = (schedule_path.name, target_monday.isoformat())
    with SENT_LOG_FILE.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("schedule"), row.get("target_monday")) == want:
                return True
    return False


def record_sent(schedule_path, target_monday):
    """Append a 'posted' marker so later retry/backup runs skip this week."""
    new_file = not SENT_LOG_FILE.exists()
    with SENT_LOG_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["schedule", "target_monday", "iso_time"])
        writer.writerow([
            schedule_path.name,
            target_monday.isoformat(),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ])


# --- Main -----------------------------------------------------------------

def main():
    load_dotenv()  # local convenience; harmless on GitHub Actions (no .env there)

    # Parse arguments: an optional date and optional flags, in any order.
    #   --dry-run           show what WOULD be posted, without contacting Telegram.
    #   --schedule PATH     use a different schedule CSV (default: schedule.csv).
    #   --images PATH       use a different images folder (default: images/).
    # --schedule/--images let one script drive multiple content streams
    # (e.g. the Wednesday poster), so all the send logic stays in one place.
    global SCHEDULE_FILE, IMAGES_DIR
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    force = "--force" in args  # post even if sent_log.csv says this week is done

    def take_value(flag):
        """Pull the value following `flag` out of args, or return None."""
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                _fail(f"{flag} needs a value")
            value = args[i + 1]
            del args[i : i + 2]
            return value
        return None

    schedule_arg = take_value("--schedule")
    images_arg = take_value("--images")
    if schedule_arg:
        SCHEDULE_FILE = (BASE_DIR / schedule_arg).resolve()
    if images_arg:
        IMAGES_DIR = (BASE_DIR / images_arg).resolve()

    date_args = [a for a in args if not a.startswith("--")]

    if date_args:
        target = _parse_date(date_args[0])
        if target is None:
            _fail(f"bad date argument '{date_args[0]}'; use YYYY-MM-DD or M/D/YYYY")
    else:
        target = date.today()
    target_monday = monday_of(target)

    rows = load_schedule()
    row = pick_row(rows, target_monday)

    if not row["image"]:
        _fail(f"row dated {row['date']} has no image filename")
    image_path = resolve_image(row["image"])
    if image_path is None:
        _fail(f"image not found in {IMAGES_DIR} for: {row['image']}")

    message = row["message"]

    if dry_run:
        print("--- DRY RUN (nothing sent) ---")
        print(f"For date:  {target}  (week of Monday {target_monday})")
        print(f"Would post image: {image_path.name}")
        print(f"Caption:\n{message}")
        print("--- end dry run ---")
        return

    # Idempotency guard: if this week's post already went out (recorded in
    # sent_log.csv, which the workflow commits back), skip. This is what makes
    # the backup cron times safe — the first successful run posts, later runs
    # for the same week no-op instead of double-posting. --force overrides it.
    if not force and already_sent(SCHEDULE_FILE, target_monday):
        print(
            f"Already posted {SCHEDULE_FILE.name} for week of {target_monday}; "
            "skipping (use --force to post anyway)."
        )
        return

    # Real send path needs credentials.
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token:
        _fail("TELEGRAM_BOT_TOKEN is not set")
    if not chat_id:
        _fail("TELEGRAM_CHAT_ID is not set")

    print(f"Posting topic for {row['date']}: image={image_path.name}")

    if len(message) <= CAPTION_LIMIT:
        message_id = send_photo(token, chat_id, image_path, message)
    else:
        # Caption too long: photo carries the first chunk, rest as a follow-up.
        message_id = send_photo(token, chat_id, image_path, message[:CAPTION_LIMIT])
        send_message(token, chat_id, message[CAPTION_LIMIT:])

    # Pin the photo (the post's main message) so it sits at the top of the chat.
    # Needs the bot to be an admin with 'Pin Messages'; a failed pin stops the run.
    if message_id is not None:
        pin_message(token, chat_id, message_id)

    # Record the message id so the participation classifier can recognise
    # "replied to the weekly topic" as English practice. Never let a problem
    # here stop the post — it's already sent.
    try:
        import participation
        kind = "wednesday" if "wednesday" in SCHEDULE_FILE.name.lower() else "topic"
        participation.record_topic_post(message_id, chat_id, kind)
    except Exception as exc:  # noqa: BLE001
        print(f"(could not record topic message id: {exc})", file=sys.stderr)

    # Mark this week done so any repeat run (delayed cron, backup time, manual
    # re-trigger) skips instead of posting again.
    record_sent(SCHEDULE_FILE, target_monday)

    print("Done.")


if __name__ == "__main__":
    main()
