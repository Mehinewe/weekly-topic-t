"""Validate posting content offline without credentials or state changes."""
import string
from zoneinfo import ZoneInfo
import participation as P
import send_weekly_awards as awards
import send_weekly_topic as topics
import send_wednesday_idiom as idioms
import send_shadowing as shadowing
import shadowing as shadow_core


def validate_participation(cfg, messages):
    for key, lower, upper in (("required_days", 1, 7), ("clear_weeks", 1, 520),
                              ("max_removals_per_run", 0, 100000)):
        if type(cfg.get(key)) is not int or not lower <= cfg[key] <= upper:
            raise ValueError(f"Invalid participation setting: {key}")
    ZoneInfo(cfg["week_timezone"])
    if type(cfg.get("auto_removal")) is not bool:
        raise ValueError("auto_removal must be a boolean")
    for key in ("reminders", "evaluation"):
        if type(cfg["enabled"].get(key)) is not bool:
            raise ValueError(f"enabled.{key} must be a boolean")
    for key in ("first", "second"):
        value = cfg["reminders"][key]["at_most_active_days"]
        if type(value) is not int or not 0 <= value <= 7:
            raise ValueError(f"Invalid reminder threshold: {key}")
    if cfg.get("tracking_starts") and P.parse_date(cfg["tracking_starts"]) is None:
        raise ValueError("Invalid tracking_starts date")
    if not isinstance(messages, dict):
        raise ValueError("Participation messages must be an object")
    for key in ("reminder_first", "reminder_second", "warning_strike1",
                "strike_cleared", "removal_notice"):
        text = messages.get(key)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Missing participation message: {key}")
        # Parsing catches broken braces without printing potentially private text.
        try:
            list(string.Formatter().parse(text))
        except ValueError:
            raise ValueError(f"Malformed template: {key}") from None


def validate_awards(rows):
    keys = set()
    for row in rows:
        if row["key"] in keys:
            raise ValueError("Duplicate award key")
        keys.add(row["key"])
        if row["metric"] not in ("video", "voice", "social"):
            raise ValueError(f"Invalid metric for award {row['key']}")
        if not row["message"] or not row["badge_type"] or awards.resolve_media(row["gif"]) is None:
            raise ValueError(f"Missing content or media for award {row['key']}")


def validate_shadowing(cfg):
    settings = shadow_core.settings(cfg)
    if type(settings["enabled"]) is not bool:
        raise ValueError("shadowing.enabled must be a boolean")
    if type(settings["ranking_size"]) is not int or not 1 <= settings["ranking_size"] <= 10:
        raise ValueError("shadowing.ranking_size must be an integer from 1 to 10")
    messages = shadow_core.load_messages()
    for key, text in messages.items():
        if key.startswith("_"):
            continue
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Missing shadowing message: {key}")
        try:
            list(string.Formatter().parse(text))
        except ValueError:
            raise ValueError(f"Malformed shadowing template: {key}") from None
        if "UTC" in text:
            raise ValueError(f"Shadowing message {key} says UTC; members should see GMT")
    shadow_core.load_state()
    return len(shadowing.load_schedule())


def main():
    rows = topics.load_schedule()
    weeks = set()
    for row in rows:
        week = topics.monday_of(row["date"])
        if week in weeks:
            raise ValueError(f"Duplicate Monday schedule week: {week}")
        weeks.add(week)
        if not row["message"] or not row["image"]:
            raise ValueError(f"{week}: missing message or image")
        if topics.resolve_image(row["image"]) is None:
            raise ValueError(f"{week}: missing image {row['image']}")
    lessons = idioms.load_schedule()
    award_rows = awards.load_awards()
    validate_awards(award_rows)
    cfg = P.load_config()
    validate_participation(cfg, P.load_messages())
    shadowing_weeks = validate_shadowing(cfg)
    print(f"Validated {len(rows)} topics, {len(lessons)} Wednesday lessons, "
          f"{len(award_rows)} awards, {shadowing_weeks} shadowing weeks, and "
          "reminder/evaluation configuration.")


if __name__ == "__main__":
    main()
