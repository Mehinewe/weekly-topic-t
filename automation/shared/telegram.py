"""Shared posting helpers. Sending requests are never retried automatically."""
import sys
import requests

API_TIMEOUT = 30

def _fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)

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


