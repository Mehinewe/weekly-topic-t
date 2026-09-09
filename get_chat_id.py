"""
Find your Telegram group chat id (one-time helper).
===================================================

Steps:
  1. Make sure your bot has been ADDED to the group.
  2. Send any message in the group (e.g. "hello") so Telegram has an update.
     For an existing group you may also need to remove + re-add the bot, or
     promote it to admin, for getUpdates to see group messages.
  3. Set TELEGRAM_BOT_TOKEN and run:  python get_chat_id.py
  4. Copy the chat id shown for your group (a negative number) into
     TELEGRAM_CHAT_ID.

Note: getUpdates won't work while a webhook is set, and it only returns
recent updates. If nothing shows, send a fresh message and re-run.
"""

import os
import sys

import requests


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("ERROR: set TELEGRAM_BOT_TOKEN first.", file=sys.stderr)
        sys.exit(1)

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    resp = requests.get(url, timeout=30)
    data = resp.json()

    if not data.get("ok"):
        print(f"ERROR: {data.get('description', resp.text)}", file=sys.stderr)
        sys.exit(1)

    updates = data.get("result", [])
    if not updates:
        print(
            "No updates found. Send a message in the group, then run this again.\n"
            "Tip: if the bot is in the group but nothing appears, promote it to "
            "admin or remove and re-add it, then post a new message."
        )
        return

    # Different update types carry the chat in different fields (a normal
    # message, a channel post, or membership events like being promoted/added).
    chat_carriers = (
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "my_chat_member",
        "chat_member",
    )

    seen = {}
    for upd in updates:
        chat = {}
        for key in chat_carriers:
            payload = upd.get(key)
            if payload and payload.get("chat"):
                chat = payload["chat"]
                break
        chat_id = chat.get("id")
        if chat_id is None or chat_id in seen:
            continue
        seen[chat_id] = chat
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
        print(f"chat id: {chat_id}   type: {chat.get('type')}   name: {title}")

    if not seen:
        print("Updates were found, but none contained a group chat. Send a plain "
              "text message in the group and run this again.")

    print(
        "\nUse the negative id for your group as TELEGRAM_CHAT_ID "
        "(groups/supergroups are negative)."
    )


if __name__ == "__main__":
    main()
