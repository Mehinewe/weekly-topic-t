"""
Weekly Awards — Monday poster
=============================

Reads the activity log built up by `log_activity.py`, finds last week's top
contributor in each award category, and posts a celebratory GIF + message with
a "Get Your Badge Avatar" button to the Telegram group.

Run manually for testing:
    python send_weekly_awards.py --dry-run        # show who would win, send nothing
    python send_weekly_awards.py 2026-06-22 --dry-run   # pretend "today" is that date
    python send_weekly_awards.py                  # real post for last week

Award winners are computed from `activity_log.csv`:
    🦈 Video Shark      — most videos + video bubbles
    🎙️ Voice Legend     — most voice messages
    🦋 Social Butterfly — most replies to other people (falls back to most
                          messages if nobody replied)

Required environment variables (real send only):
    TELEGRAM_BOT_TOKEN   the token from @BotFather
    TELEGRAM_CHAT_ID     the group's chat id (negative number)
    BADGE_APP_URL        (optional) base URL of the badge avatar mini app;
                         defaults to the GitHub Pages URL below.

Optional — external avatar hosting (FTP, e.g. a Hostinger subdomain). Without
these the personalised avatar step is skipped and the button falls back to the
manual-upload page, so no winner photo is ever written to the repo:
    AVATAR_FTP_HOST       FTP server, e.g. ftp.yourdomain.com
    AVATAR_FTP_USER       FTP account username
    AVATAR_FTP_PASSWORD   FTP account password
    AVATAR_FTP_DIR        (optional) target dir from the FTP root, e.g.
                          domains/badges.yourdomain.com/public_html/av
    AVATAR_FTP_PORT       (optional) default 21
    AVATAR_FTP_TLS        (optional) default "true" = explicit FTPS with cert
                          verification; "noverify" = FTPS encrypted but skip
                          cert check (needed for shared hosts whose cert is for
                          the server FQDN, not your IP); "false" = plain FTP
    AVATAR_PUBLIC_BASE    public URL the dir is served at, e.g.
                          https://badges.yourdomain.com/av
"""

import csv
import ftplib
import html
import io
import os
import secrets
import ssl
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# --- Configuration --------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "activity_log.csv"
AWARDS_FILE = BASE_DIR / "awards.csv"
BADGES_DIR = BASE_DIR / "badges"

# Shared with send_weekly_topic.py: records which (schedule, week) posts have
# already gone out, keyed here by "awards.csv", so a delayed/duplicate cron
# run for the same week skips instead of double-posting.
SENT_LOG_FILE = BASE_DIR / "sent_log.csv"
SENT_LOG_KEY = "awards.csv"

# Personalised badge avatars contain the winner's face, so they are NEVER
# committed to this (public) repo. They are built to a temp file, uploaded by
# FTP to an external subdomain, and the temp file is deleted per post.
AVATAR_TMP_DIR = BADGES_DIR

# Where the badge-avatar mini app is hosted (GitHub Pages). The award button
# links here with ?type=<badge_type>. Override with the BADGE_APP_URL env var.
DEFAULT_BADGE_APP_URL = "https://mehinewe.github.io/weekly-topic-telegram/"

CAPTION_LIMIT = 1024
API_TIMEOUT = 30

# Which message types count toward each metric.
VIDEO_TYPES = {"video"}
VOICE_TYPES = {"voice"}


# --- Helpers --------------------------------------------------------------

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
    return d - timedelta(days=d.weekday())


_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")


def _parse_date(date_str):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


ANIMATED_EXTS = (".gif", ".mp4", ".webp")
IMAGE_EXTS = (".png", ".jpg", ".jpeg")


def resolve_media(name):
    """Find the badge file for `name` in badges/, tolerating the extension.

    Accepts both animations (.gif/.mp4/.webp) and plain images (.png/.jpg), so
    a static badge picture works just as well as an animated one. A CSV that
    says shark.gif still matches shark.png on disk.
    """
    exact = BADGES_DIR / name
    if exact.exists():
        return exact
    stem = Path(name).stem
    for candidate in sorted(BADGES_DIR.glob(stem + ".*")):
        if candidate.suffix.lower() in ANIMATED_EXTS + IMAGE_EXTS:
            return candidate
    return None


def load_awards():
    """Read awards.csv into a list of template dicts."""
    if not AWARDS_FILE.exists():
        _fail(f"awards file not found: {AWARDS_FILE}")
    awards = []
    with AWARDS_FILE.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"key", "metric", "gif", "message", "badge_type"}
        if not required.issubset(set(reader.fieldnames or [])):
            _fail(
                "awards.csv must have columns: key, metric, gif, message, "
                f"badge_type (found: {reader.fieldnames})"
            )
        for raw in reader:
            key = (raw.get("key") or "").strip()
            if not key:
                continue
            awards.append({
                "key": key,
                "metric": (raw.get("metric") or "").strip().lower(),
                "gif": (raw.get("gif") or "").strip(),
                "message": (raw.get("message") or "").strip(),
                "badge_type": (raw.get("badge_type") or "").strip(),
            })
    if not awards:
        _fail("awards.csv has no usable rows")
    return awards


def load_week_rows(week_monday):
    """Return the logged rows for the given week (Monday date).

    A missing log is not an error — it just means the logger hasn't recorded
    anything yet (e.g. the first days after setup). Treat it as "no activity"
    so the run ends cleanly instead of flagging a failure.
    """
    if not LOG_FILE.exists():
        print(f"No activity log yet ({LOG_FILE.name} doesn't exist) — "
              "the logger hasn't recorded anything so far.")
        return []
    wanted = week_monday.isoformat()
    rows = []
    with LOG_FILE.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("week_monday") == wanted:
                rows.append(row)
    return rows


def resolve_name(token, chat_id, user_id, _cache={}):
    """Look up a member's FIRST name live via getChatMember.

    The activity log stores only anonymous numeric ids, so names are fetched
    here, only for the handful of winners, right before posting. Returns just
    the first name (friendlier in the announcement). Falls back to "User <id>"
    if the lookup fails (e.g. the member left the group).
    """
    if user_id in _cache:
        return _cache[user_id]
    name = f"User {user_id}"
    try:
        url = f"https://api.telegram.org/bot{token}/getChatMember"
        resp = requests.get(
            url, params={"chat_id": chat_id, "user_id": user_id}, timeout=API_TIMEOUT
        )
        body = resp.json()
        if body.get("ok"):
            user = body["result"].get("user", {})
            first = (user.get("first_name") or "").strip()
            name = first or user.get("username") or name
    except (ValueError, requests.RequestException, KeyError):
        pass
    _cache[user_id] = name
    return name


def get_profile_photo(token, user_id):
    """Download the member's current profile photo as a PIL image, or None.

    Returns None if they have no photo, it's hidden from the bot, or anything
    goes wrong — the caller then just sends the static badge.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        base = f"https://api.telegram.org/bot{token}"
        r = requests.get(
            f"{base}/getUserProfilePhotos",
            params={"user_id": user_id, "limit": 1}, timeout=API_TIMEOUT,
        ).json()
        if not r.get("ok") or r["result"].get("total_count", 0) == 0:
            return None
        # photos[0] is one photo in several sizes; the last is the largest.
        file_id = r["result"]["photos"][0][-1]["file_id"]
        f = requests.get(
            f"{base}/getFile", params={"file_id": file_id}, timeout=API_TIMEOUT
        ).json()
        if not f.get("ok"):
            return None
        file_path = f["result"]["file_path"]
        data = requests.get(
            f"https://api.telegram.org/file/bot{token}/{file_path}",
            timeout=API_TIMEOUT,
        ).content
        return Image.open(io.BytesIO(data))
    except (requests.RequestException, ValueError, KeyError, IndexError, OSError):
        return None


def mention_html(user_id, first_name):
    """Build a clickable Telegram mention that pings the user.

    Uses an inline tg://user link, which notifies the person even if they have
    no @username (it works because the bot has seen them in the group).
    """
    return f'<a href="tg://user?id={user_id}">{html.escape(first_name, quote=False)}</a>'


def tally(rows, metric):
    """Count contributions per user for a metric. Returns a Counter."""
    counts = Counter()
    if metric == "video":
        for row in rows:
            if row.get("type") in VIDEO_TYPES:
                counts[row["user_id"]] += 1
    elif metric == "voice":
        for row in rows:
            if row.get("type") in VOICE_TYPES:
                counts[row["user_id"]] += 1
    elif metric == "social":
        for row in rows:
            if row.get("is_reply") == "1":
                counts[row["user_id"]] += 1
        if not counts:  # nobody replied — fall back to most messages overall
            for row in rows:
                counts[row["user_id"]] += 1
    else:
        _fail(f"unknown metric '{metric}' in awards.csv")
    return counts


def pick_winner(counts, exclude=()):
    """Return (user_id, count) for the top contributor, or None.

    Users in `exclude` are skipped, so someone who already won another award
    can't win this one too (one person = one badge). Ties are broken by
    user_id so the result is deterministic.
    """
    eligible = {uid: c for uid, c in counts.items() if uid not in exclude}
    if not eligible:
        return None
    best = max(eligible.values())
    if best <= 0:
        return None
    winners = sorted(uid for uid, c in eligible.items() if c == best)
    return winners[0], best


# --- Idempotency guard ----------------------------------------------------
# Mirrors send_weekly_topic.py's guard: GitHub cron is best-effort, so awards.yml
# gets backup cron times too. Recording each successful post (keyed by
# "awards.csv" + the week's Monday) lets a later retry for the same week no-op.

def already_posted(target_monday):
    """True if awards for this week were already posted, per sent_log.csv."""
    if not SENT_LOG_FILE.exists():
        return False
    want = (SENT_LOG_KEY, target_monday.isoformat())
    with SENT_LOG_FILE.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("schedule"), row.get("target_monday")) == want:
                return True
    return False


def record_posted(target_monday):
    """Append a 'posted' marker so later retry/backup runs skip this week."""
    new_file = not SENT_LOG_FILE.exists()
    with SENT_LOG_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["schedule", "target_monday", "iso_time"])
        writer.writerow([
            SENT_LOG_KEY,
            target_monday.isoformat(),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ])


# --- External avatar hosting (FTP to a subdomain) ----------------------
# The personalised avatar is the winner's photo, so it must not land in this
# public repo. It is uploaded by FTP to a subdomain's web folder under a
# random, unguessable name; only the winner gets that URL (in their button).

class _ReuseTLS(ftplib.FTP_TLS):
    """FTP_TLS that resumes the control-channel TLS session on the data
    connection. Servers like Hostinger's require this, otherwise the STOR
    fails with '425 ... TLS session of data connection not resumed'."""

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host,
                session=self.sock.session,  # type: ignore[attr-defined]
            )
        return conn, size


def _ftp_chdir(ftp, path):
    """cwd into `path` segment by segment, creating dirs that don't exist."""
    for part in path.strip("/").split("/"):
        if not part:
            continue
        try:
            ftp.cwd(part)
        except ftplib.error_perm:
            ftp.mkd(part)
            ftp.cwd(part)


def upload_avatar(local_path, award_key):
    """Upload a built avatar PNG by FTP; return its public URL.

    Returns None (and prints a warning) if FTP isn't configured or the upload
    fails — the caller then falls back to the manual-upload page, so a hosting
    hiccup never blocks the award post.
    """
    host = os.environ.get("AVATAR_FTP_HOST")
    user = os.environ.get("AVATAR_FTP_USER")
    password = os.environ.get("AVATAR_FTP_PASSWORD")
    public_base = (os.environ.get("AVATAR_PUBLIC_BASE") or "").rstrip("/")
    if not (host and user and password and public_base):
        print("  (AVATAR_FTP_* / AVATAR_PUBLIC_BASE not set — "
              "skipping avatar upload, button uses upload page)")
        return None

    remote_dir = os.environ.get("AVATAR_FTP_DIR") or ""
    port = int(os.environ.get("AVATAR_FTP_PORT") or 21)
    # AVATAR_FTP_TLS: "true" (default) = FTPS, verify cert;
    #                 "noverify"/"insecure" = FTPS, encrypted but don't verify
    #                   (shared hosts like Hostinger present a cert for the
    #                   server FQDN, not the IP/subdomain you connect to);
    #                 "false" = plain FTP (password sent in the clear).
    tls_mode = os.environ.get("AVATAR_FTP_TLS", "true").strip().lower()
    plain = tls_mode in ("false", "0", "no", "off")
    verify = tls_mode not in ("noverify", "insecure", "no-verify", "skip-verify")
    # Random name: no user id, not guessable from outside.
    obj = f"{award_key}-{secrets.token_urlsafe(9)}.png"

    if plain:
        ftp = ftplib.FTP()
    else:
        ctx = ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        ftp = _ReuseTLS(context=ctx)
    try:
        ftp.connect(host, port, timeout=API_TIMEOUT)
        ftp.login(user, password)
        if isinstance(ftp, ftplib.FTP_TLS):
            ftp.prot_p()  # encrypt the data channel too
        ftp.set_pasv(True)
        if remote_dir:
            _ftp_chdir(ftp, remote_dir)
        with local_path.open("rb") as fh:
            ftp.storbinary(f"STOR {obj}", fh)
    except ftplib.all_errors as exc:  # includes OSError / ssl.SSLError / EOFError
        print(f"  (avatar FTP upload failed: {exc}; button uses upload page)",
              file=sys.stderr)
        return None
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001 - already failed / not connected
            pass
    return f"{public_base}/{obj}"


# --- Telegram -------------------------------------------------------------

def send_badge(token, chat_id, media_path, caption, button_url):
    """Send the badge (animation or static image) with caption + inline button.

    Uses sendAnimation for .gif/.mp4/.webp and sendPhoto for .png/.jpg, so the
    award works whether you supply an animated GIF or a plain picture.
    """
    animated = media_path.suffix.lower() in ANIMATED_EXTS
    method, field = ("sendAnimation", "animation") if animated else ("sendPhoto", "photo")
    url = f"https://api.telegram.org/bot{token}/{method}"
    reply_markup = (
        '{"inline_keyboard":[[{"text":"🌟 Get Your Badge Avatar 🌟",'
        f'"url":"{button_url}"}}]]}}'
    )
    with media_path.open("rb") as media:
        resp = requests.post(
            url,
            data={
                "chat_id": chat_id,
                "caption": caption,
                "parse_mode": "HTML",  # makes the mention link clickable + pings
                "reply_markup": reply_markup,
            },
            files={field: media},
            timeout=API_TIMEOUT,
        )
    _check(resp, method)


def send_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=API_TIMEOUT,
    )
    _check(resp, "sendMessage")


def _check(resp, what):
    try:
        body = resp.json()
    except ValueError:
        _fail(f"{what}: non-JSON response (HTTP {resp.status_code}): {resp.text[:300]}")
    if not body.get("ok"):
        _fail(f"{what} failed: {body.get('description', resp.text[:300])}")
    print(f"{what} OK")


# --- Main -----------------------------------------------------------------

def main():
    load_dotenv()

    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    this_week = "--this-week" in args  # testing: award the CURRENT week, not last
    force = "--force" in args  # post even if sent_log.csv says this week is done
    date_args = [a for a in args if not a.startswith("--")]

    if date_args:
        today = _parse_date(date_args[0])
        if today is None:
            _fail(f"bad date argument '{date_args[0]}'; use YYYY-MM-DD or M/D/YYYY")
    else:
        today = date.today()

    # Normally awards posted on Monday celebrate the PREVIOUS week. With
    # --this-week (a testing aid) they celebrate the week in progress, so you
    # can see results right after logging some activity instead of waiting.
    if this_week:
        target_monday = monday_of(today)
    else:
        target_monday = monday_of(today) - timedelta(days=7)
    last_week_monday = target_monday

    awards = load_awards()
    rows = load_week_rows(last_week_monday)
    app_url = (os.environ.get("BADGE_APP_URL") or DEFAULT_BADGE_APP_URL).rstrip("/")
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    print(f"Computing awards for week of Monday {last_week_monday} "
          f"({len(rows)} logged messages).")
    if not rows:
        print("No activity logged for that week — nothing to post.")
        return

    # Decide every winner first, so a dry run shows the full picture. Each
    # winner is excluded from later awards so one person can't win more than
    # one badge — a runner-up takes the next award instead.
    planned = []
    taken = set()
    for award in awards:
        result = pick_winner(tally(rows, award["metric"]), exclude=taken)
        if result is None:
            print(f"  {award['key']}: no qualifying activity — skipping.")
            continue
        uid, count = result
        taken.add(uid)
        planned.append((award, uid, count))

    if not planned:
        print("No awards have a winner this week — nothing to post.")
        return

    if dry_run:
        # Names come from a live lookup, which needs a token; without one we
        # just show the anonymous id so dry runs still work offline.
        _ftp_ready = all(os.environ.get(k) for k in
                         ("AVATAR_FTP_HOST", "AVATAR_FTP_USER",
                          "AVATAR_FTP_PASSWORD", "AVATAR_PUBLIC_BASE"))
        avatar_host = (f"FTP -> {os.environ['AVATAR_PUBLIC_BASE']}" if _ftp_ready
                       else "NOT configured — button falls back to upload page")
        print("--- DRY RUN (nothing sent) ---")
        print(f"  avatar hosting: {avatar_host}")
        for award, uid, count in planned:
            name = resolve_name(token, chat_id, uid) if (token and chat_id) else f"User {uid}"
            photo = "?"
            if token:
                photo = "yes (flip GIF)" if get_profile_photo(token, uid) else "no (static badge)"
            print(f"  {award['key']}: {name} (id {uid}) — {count} — gif={award['gif']}")
            print(f"    profile photo: {photo}")
            print(f"    fallback button -> {app_url}/?type={award['badge_type']}")
        print("--- end dry run ---")
        return

    # Idempotency guard: if awards for this week already went out (recorded in
    # sent_log.csv, which the workflow commits back), skip. This is what makes
    # backup cron times safe. --force overrides it.
    if not force and already_posted(target_monday):
        print(
            f"Already posted awards for week of {target_monday}; "
            "skipping (use --force to post anyway)."
        )
        return

    if not token:
        _fail("TELEGRAM_BOT_TOKEN is not set")
    if not chat_id:
        _fail("TELEGRAM_CHAT_ID is not set")

    for award, uid, count in planned:
        media_path = resolve_media(award["gif"])
        if media_path is None:
            print(f"  WARNING: badge file not found for {award['key']} "
                  f"({award['gif']}) — skipping this award.", file=sys.stderr)
            continue
        first = resolve_name(token, chat_id, uid)
        # parse_mode is HTML, so escape the (user-editable) award text and drop
        # in a clickable mention in place of {name}.
        caption = html.escape(award["message"], quote=False).replace(
            "{name}", mention_html(uid, first)
        )
        # Default button: the manual-upload page for this badge type (used when
        # we can't get the winner's photo).
        button_url = f"{app_url}/?type={award['badge_type']}"
        print(f"Posting {award['key']} for {first} ({count}).")

        # Fetch the winner's photo once, reused for the flip GIF and the avatar.
        photo = (get_profile_photo(token, uid)
                 if media_path.suffix.lower() in IMAGE_EXTS else None)

        send_path, temp_gif, temp_avatar = media_path, None, None
        if photo is not None:
            # 1. Personalised flip GIF (badge -> their photo) as the post media.
            try:
                from make_award_gif import build_flip_gif
                temp_gif = BADGES_DIR / f"_flip_{award['key']}.gif"
                build_flip_gif(media_path, photo, temp_gif)
                send_path = temp_gif
                print("  personalised with profile photo (flip GIF)")
            except Exception as exc:  # noqa: BLE001 - never block the post
                print(f"  (flip GIF failed: {exc}; sending static badge)",
                      file=sys.stderr)
            # 2. Badge avatar (photo + corner emblem): built to a temp file,
            #    uploaded to external storage, never written to the repo.
            try:
                from make_award_gif import build_badge_avatar
                temp_avatar = AVATAR_TMP_DIR / f"_avatar_{award['key']}.png"
                build_badge_avatar(media_path, photo, temp_avatar)
                hosted = upload_avatar(temp_avatar, award["key"])
                if hosted:
                    button_url = hosted
                    print(f"  built + uploaded badge avatar -> {hosted}")
            except Exception as exc:  # noqa: BLE001
                print(f"  (avatar build failed: {exc}; button uses upload page)",
                      file=sys.stderr)
        else:
            print("  no profile photo available — sending static badge")

        try:
            if len(caption) <= CAPTION_LIMIT:
                send_badge(token, chat_id, send_path, caption, button_url)
            else:
                send_badge(token, chat_id, send_path, caption[:CAPTION_LIMIT], button_url)
                send_message(token, chat_id, caption[CAPTION_LIMIT:])
        finally:
            for tmp in (temp_gif, temp_avatar):
                if tmp is not None and tmp.exists():
                    tmp.unlink()

    # Mark this week done so any repeat run (delayed cron, backup time, manual
    # re-trigger) skips instead of posting again.
    record_posted(target_monday)

    print("Done.")


if __name__ == "__main__":
    main()
