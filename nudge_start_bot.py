"""Pre-launch nudge — remind members who haven't started the bot to do so.

One-off campaign before the participation rule goes live (LAUNCH date below).
Runs Thu–Sun via nudge-start-bot.yml. Every run it re-checks the roster and
posts the Announcement.jpg banner + an @-mention list of members the bot has
seen post but who have NOT opened a DM with it (probed silently with
sendChatAction — no visible message). No-ops once that list is empty or once
enforcement has begun. Never pins.

DELETE this file and .github/workflows/nudge-start-bot.yml after launch.

    python nudge_start_bot.py --dry-run    # print who'd be nudged, send nothing
"""
import csv
import html
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
LAUNCH = date(2026, 9, 14)           # == participation_config tracking_starts
BANNER = BASE / "Announcement.jpg"
ACTIVITY = BASE / "activity_log.csv"
API_TIMEOUT = 30
IN_GROUP = ("member", "administrator", "creator", "restricted")


def load_dotenv():
    p = BASE / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main():
    dry = "--dry-run" in sys.argv[1:]
    load_dotenv()

    if date.today() >= LAUNCH:
        print(f"Enforcement started ({LAUNCH}); the nudge campaign is over. "
              "Safe to delete nudge_start_bot.py + nudge-start-bot.yml.")
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        if dry:
            print("(no credentials — dry run can't resolve the roster)")
            return
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    api = f"https://api.telegram.org/bot{token}"

    seen, ids = set(), []
    with ACTIVITY.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            u = r.get("user_id")
            if u and u not in seen:
                seen.add(u)
                ids.append(u)

    not_started = []
    for uid in ids:
        cm = requests.post(f"{api}/getChatMember",
                           data={"chat_id": chat_id, "user_id": uid},
                           timeout=API_TIMEOUT).json()
        if not cm.get("ok") or cm["result"].get("status") not in IN_GROUP:
            continue
        u = cm["result"]["user"]
        name = (u.get("first_name", "") + " " + u.get("last_name", "")).strip() or f"member {uid}"
        probe = requests.post(f"{api}/sendChatAction",
                              data={"chat_id": uid, "action": "typing"},
                              timeout=API_TIMEOUT).json()
        if not probe.get("ok"):
            not_started.append((uid, name))
        time.sleep(0.3)

    print(f"{len(ids)} known members; {len(not_started)} have not started the bot.")
    for uid, name in not_started:
        print(f"  - {name} ({uid})")

    if not not_started:
        print("Everyone the bot knows has started it — nothing sent. "
              "Safe to delete nudge_start_bot.py + nudge-start-bot.yml.")
        return

    mentions = " · ".join(
        f'<a href="tg://user?id={uid}">{html.escape(name)}</a>'
        for uid, name in not_started
    )
    days_left = (LAUNCH - date.today()).days
    caption = (
        f"⏰ <b>{days_left} day(s) until the new rule starts "
        f"(Monday 14 September)</b>\n\n"
        "If Mervi tags you below, you still need to <b>open a private chat with "
        "Mervi and tap START</b> — otherwise your Wednesday &amp; Friday "
        "reminders can't reach you (Mervi can't message you first).\n\n"
        "<b>Not tagged?</b> If you're in this group, send any message here so "
        "Mervi can track you, then start the bot too.\n\n"
        "Full rule is pinned: <b>3 active days per week</b>, voice or video \U0001f4aa"
    )

    if dry:
        print("\n--- DRY RUN (nothing sent) ---")
        print(caption)
        print("\n\U0001f449 " + mentions)
        return

    with BANNER.open("rb") as img:
        r = requests.post(f"{api}/sendPhoto",
                          data={"chat_id": chat_id, "caption": caption,
                                "parse_mode": "HTML"},
                          files={"photo": img}, timeout=60).json()
    if not r.get("ok"):
        sys.exit(f"sendPhoto failed: {r}")
    print("banner sent, message_id", r["result"]["message_id"])
    r = requests.post(f"{api}/sendMessage",
                      data={"chat_id": chat_id, "text": "\U0001f449 " + mentions,
                            "parse_mode": "HTML",
                            "disable_web_page_preview": "true"},
                      timeout=API_TIMEOUT).json()
    print("mentions sent" if r.get("ok") else f"mentions failed: {r}")


if __name__ == "__main__":
    main()
