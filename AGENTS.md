# Repository Guidelines

## Project Structure & Module Organization

This repository automates Telegram topics, awards, and participation management with Python scripts and GitHub Actions; there is no application server.

- Root scripts include `send_weekly_topic.py`, `send_weekly_awards.py`, and `log_activity.py`. Shared participation logic lives in `participation.py`; bot commands live in `participation_commands.py`.
- Root CSV/JSON files contain schedules, configuration, and persistent runtime state.
- `images/` holds Monday artwork; `images_idioms/` holds Wednesday illustrations; `badges/` holds award assets. The old `images_wednesday/` is retired.
- `docs/index.html` is the static badge-avatar app served through GitHub Pages.
- `.github/workflows/` contains scheduled jobs. Consult `WEEKLY_GUIDE.md`, `AWARDS_GUIDE.md`, and `PARTICIPATION_GUIDE.md` for feature details.

## Build, Test, and Development Commands

Use Python 3.12, matching Actions. Run commands from the repository root; no build step is required.

```bash
python -m pip install -r requirements.txt
python participation.py
python send_weekly_topic.py 2026-06-22 --dry-run
python send_weekly_awards.py --dry-run
python evaluate_participation.py --dry-run
python send_reminders.py --which wed --dry-run
```

These install dependencies, run classifier checks, and preview posts. Preview Wednesday with `python send_wednesday_idiom.py --phase challenge --date 2026-09-16 --dry-run`; use `--phase reveal` for the answer. Content lives in `idioms_wednesday.json`; see `IDIOM_GUIDE.md`.

## Coding Style & Naming Conventions

Follow existing Python style: four-space indentation, `snake_case` functions and variables, and `UPPER_SNAKE_CASE` constants. Resolve data paths relative to `__file__`. No formatter or linter is configured.

Preserve UTF-8 text and emoji. Schedule CSVs use `date,image,message`; prefer `YYYY-MM-DD` dates and correctly quote multiline captions. Match artwork filenames to schedule entries.

## Testing Guidelines

Run `python -m unittest discover -s tests -v` for offline Wednesday posting regression tests. No coverage threshold is configured. Run relevant previews too. For classifier edits, extend the samples in `participation.py` and inspect results: failures print `FAIL` without a failing exit code. Verify dry runs leave state unchanged. Check badge-app changes in a browser.

## Commit & Pull Request Guidelines

History uses concise, descriptive subjects, sometimes prefixed by feature, such as `Participation:` or `Nudge:`. Use imperative wording and focused commits.

PRs should describe behavior changes, list validation commands and results, link relevant issues, and include screenshots for visual changes. Explain schedule, configuration, and state-schema changes.

## Security & State Handling

Keep credentials in gitignored `.env` or Actions secrets. Never commit member names, message text, or generated personal avatars. Preserve intentionally tracked logs and state. Keep `log_activity.py` as the single Telegram update poller, and preserve idempotency guards and workflow concurrency.
