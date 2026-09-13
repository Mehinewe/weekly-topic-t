"""Validate posting content offline without credentials or state changes."""
import send_weekly_topic as topics
import send_wednesday_idiom as idioms


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
    print(f"Validated {len(rows)} Monday topics and {len(lessons)} Wednesday lessons.")


if __name__ == "__main__":
    main()
