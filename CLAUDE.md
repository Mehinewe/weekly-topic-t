# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Telegram group automation, run on GitHub Actions cron — no app server, no test suite.
Two features share the same bot token / chat id (repo secrets `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`):

1. **Weekly topic poster** — posts a prepared image + caption every Monday. Stateless,
   CSV-driven. (`send_weekly_topic.py`, `schedule.csv`, `weekly.yml`)
2. **Weekly awards** — auto-computes last week's top contributors and posts award GIFs
   with a "Get Your Badge Avatar" button. Stateful: an hourly logger records group
   activity all week, a Monday poster tallies it. (`log_activity.py` + `send_weekly_awards.py`,
   `activity_log.csv`, `awards.csv`, `log.yml` + `awards.yml`, `docs/` mini app)
3. **Participation & inactivity management** — tracks whether each member practises on
   ≥ 3 different days/week, sends Wed/Fri reminders, runs a two-strike system that can
   auto-remove chronically inactive members, and handles `/pause` + admin commands.
   Shares the `log_activity.py` poller. (`participation.py`, `participation_commands.py`,
   `send_reminders.py`, `evaluate_participation.py`, `participation_config.json` +
   `participation_messages.json`, `members.csv` et al., `reminder-wed.yml` /
   `reminder-fri.yml` / `evaluate.yml`.) Full docs: [PARTICIPATION_GUIDE.md](PARTICIPATION_GUIDE.md)

## Commands

```bash
pip install -r requirements.txt          # only dep is `requests`

# Preview without sending (no token needed — exits before the credential check):
python send_weekly_topic.py --dry-run
python send_weekly_topic.py 2026-06-22 --dry-run   # force a specific week

# Real send (needs env vars set):
python send_weekly_topic.py              # picks the current week's row
python send_weekly_topic.py 2026-06-22   # force a specific Monday

python get_chat_id.py                     # one-time helper to find the group chat id

# Participation feature — all safe to run without secrets:
python participation.py                          # classifier self-test
python evaluate_participation.py --dry-run       # preview last week's strike run
python evaluate_participation.py --dry-run --this-week
python send_reminders.py --which wed --dry-run   # preview Wednesday reminders
```

Required env vars for a real send: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
Locally these are read from a gitignored `.env` (see `load_dotenv` in the script);
on GitHub Actions they come from repo secrets of the same names.

## Architecture

The whole flow lives in [send_weekly_topic.py](send_weekly_topic.py):

1. **Week resolution** — `monday_of()` snaps any date to the Monday of its week. The
   target is today (or the CLI date arg), so the same script run any day that week
   resolves to the same row.
2. **Row selection** (`pick_row`) — matches a `schedule.csv` row by the *Monday of its
   date*, so CSV dates don't have to be exact Mondays. If no row matches the target
   week, it falls back to the **most recent past row** rather than failing — a missed
   week re-sends the latest topic.
3. **Image resolution** (`resolve_image`) — looks up the CSV's `image` value in
   `images/`, and if the exact filename is missing, retries by stem with a
   `.png/.jpg/.jpeg` swap (so `0.png` in the CSV still matches `0.jpg` on disk).
4. **Send** — `sendPhoto` with the message as caption. Telegram caps captions at 1024
   chars (`CAPTION_LIMIT`); longer messages send the first chunk as the caption and the
   remainder as a follow-up `sendMessage`.

`--dry-run` short-circuits before the credential check, so previews work without secrets.
Any error calls `_fail()` which exits non-zero so the scheduler flags the run.

## Data contract: schedule.csv

The CSV *is* the content database. Columns: `date,image,message`.
- `date` — the Monday to post (`YYYY-MM-DD` preferred; `M/D/YYYY` also parsed).
- `image` — filename under `images/`, must match (extension swap is tolerated).
- `message` — wrap in `"double quotes"`; multi-line and emoji are supported.

Emojis matter: editing the CSV in Excel strips them, so the documented workflow
(see [WEEKLY_GUIDE.md](WEEKLY_GUIDE.md)) is to edit it directly on the GitHub website.

## Scheduling

[.github/workflows/weekly.yml](.github/workflows/weekly.yml) runs the script every
Monday at 10:32 UTC (`cron: "32 10 * * 1"`). GitHub cron is always UTC. The awards
workflow runs earlier at 10:17 UTC; both use off-peak minutes to reduce scheduler delays.

**Cron is best-effort — expect delays.** GitHub runs scheduled workflows on a queue and
routinely fires them late (hours, sometimes) or, under load, drops a run entirely — this has
happened to a *primary* cron slot outright (no run object at all, not just a late one), so
the guard below isn't just theoretical. So the topic (`weekly.yml`), Wednesday
(`wednesday.yml`), and awards (`awards.yml`) workflows each have **three cron times** — a
primary plus two later catch-ups (10:32 / 12:47 / 14:47 UTC for the topic poster; 10:17 /
12:32 / 14:32 UTC for awards) — and an **idempotency guard** keeps that safe:
`send_weekly_topic.py` and `send_weekly_awards.py` each record every successful post in the
shared `sent_log.csv` (`schedule,target_monday,iso_time`, keyed by `schedule.csv` /
`schedule_wednesday.csv` / `awards.csv`) and skip a week already marked, so only the first
attempt that lands actually posts and the rest no-op. The workflows commit `sent_log.csv`
back to the repo (concurrency-guarded, `contents: write`), so like `activity_log.csv` it is
intentionally NOT gitignored. `--force` posts even if the week is marked (for an intentional
re-send).

`workflow_dispatch` allows a manual run with a `dry_run` input that **defaults to true**
(safe preview). The scheduled run always posts for real; a manual run posts only if you
untick "Dry run". The dry-run flag is wired in via the workflow's `run:` expression, not
the script.

[run_local.bat](run_local.bat) is the Windows Task Scheduler alternative (only runs while
the PC is on). It just sets the two env vars and calls the script.

## Weekly awards (the stateful feature)

Because Telegram gives a bot **no message history** and drops undelivered updates after
~24h, winner-counting can't be a once-a-week job. It's split in two:

- **`log_activity.py`** (hourly via `log.yml`) — calls `getUpdates`, advances the
  `last_update_id` saved in `activity_state.json`, and appends one row per group message
  to `activity_log.csv` (`iso_time, week_monday, user_id, type, is_reply,
  reply_to_user_id`). `type` ∈ video/voice/text/other; bot messages and other chats are
  skipped. The log stores **only anonymous numeric ids — no names** — so the repo can be
  public (Pages needs that). The workflow **commits the log + state back to the repo**
  (concurrency-guarded) so state persists across stateless runs — this is why
  `activity_log.csv` and `activity_state.json` are intentionally NOT gitignored.
- **`send_weekly_awards.py`** (Monday via `awards.yml`) — reads the rows for the
  *previous* week (`monday_of(today) - 7d`), tallies per `awards.csv` metric
  (video / voice / social), picks the top user per award, looks up that user's first name
  **live via `getChatMember`** (`resolve_name`), and posts the badge + caption (with the
  winner **@-mentioned** via a `tg://user` HTML link so they're pinged) + an inline URL
  button. Social = most replies-to-others, falling back to most messages. The `--this-week`
  flag (and workflow checkbox) targets the current week for testing.
- **`make_award_gif.py`** — Pillow image builders. `build_flip_gif` makes the personalised
  **flip GIF** (badge flips to reveal the winner's circular photo, loops); `build_badge_avatar`
  makes the **badge avatar** (the winner's photo with a circular award emblem cropped from the
  badge stamped in the corner). At post time the poster calls `get_profile_photo`
  (getUserProfilePhotos → getFile → download) once per winner; with a photo it sends the flip
  GIF and builds the avatar to a temp file, then `upload_avatar()` uploads it **by FTP** to a
  subdomain's web folder (Hostinger) under a random unguessable name and points the button
  straight at `AVATAR_PUBLIC_BASE/<name>.png`. FTPS with data-channel session reuse
  (`_ReuseTLS`) since Hostinger requires it; `AVATAR_FTP_TLS=false` forces plain FTP. With no
  photo — or if the `AVATAR_FTP_*` env vars are unset — it sends the static badge and the
  button falls back to the manual-upload page. The avatar contains the winner's face, so it
  is **never committed to this public repo**: both `badges/_flip_*.gif` and
  `badges/_avatar_*.png` temp files are deleted per post, and `docs/avatars/` is gitignored.

`awards.csv` columns: `key, metric, gif, message, badge_type`. `message` uses `{name}`;
`badge_type` must match a key in the mini app and becomes the button's `?type=` param.

## Participation & inactivity management

See [PARTICIPATION_GUIDE.md](PARTICIPATION_GUIDE.md) for the rule, rollout, and admin
commands. Key architecture points:

- **`log_activity.py` is the single Telegram ingest.** A bot has one `getUpdates`
  cursor, so activity logging, roster/join tracking, `/pause` and admin commands all
  live in that poller (now `*/10 * * * *`, up from hourly). Everything else only
  *sends*. `classify_practice()` in `participation.py` decides the `practice` 0/1
  column at log time — **message text is never stored**, so classifier tweaks are
  forward-only. Speaking-only rule: only voice / video / video-note count. Text
  never counts, not even as a reply; `count_topic_reply` / `count_member_reply`
  in `participation_config.json` (both **off**) would re-enable ≥ `reply_min_words`
  word replies to the topic / to a member.
- **All state is numeric-id-only** (like `activity_log.csv`) and committed back:
  `members.csv` (roster + strike/pause/grace state), `weekly_results.csv`,
  `pause_requests.csv`, `removals.csv`, `reminder_log.csv`,
  `participation_eval_log.csv`, `topic_posts.csv`. Names are resolved live via
  `getChatMember` only when a message is sent. `members.csv` auto-seeds from
  `activity_log.csv` on first run.
- **Strike machine** (`apply_week_result`): miss a week → Strike 1 + warning; miss
  again while Strike 1 is active → Strike 2 → kick (ban+unban); `clear_weeks`
  successful weeks clears Strike 1; any failed week resets the success counter.
  **This deployment: `clear_weeks: 1`** (one good week clears — "fast two-strike").
  `auto_removal` ships **off** (Strike 2 only flagged in the report) — turn on
  after a 1–2 week warning-only launch.
- **`evaluate_participation.py`** (Monday, `evaluate.yml`, 3 cron times, idempotent
  via `participation_eval_log.csv`) has safety gates: aborts if a week has no logged
  practice activity, if far fewer members than usual were active (unless `--force`),
  or if a run would remove more than `max_removals_per_run` (5).
- **Reminders** (`send_reminders.py --which wed|fri`, `reminder-wed.yml` /
  `reminder-fri.yml`) DM members who are behind; idempotent via `reminder_log.csv`.
  DMs only reach members who have started the bot; `dm_fallback_to_group` @-mentions
  the rest in the group.
- All participation workflows share `concurrency: group: telegram-state` with
  `log.yml` so the committed state files never race.
- `send_weekly_topic.py` now records each post's `message_id` to `topic_posts.csv`
  (wrapped in try/except) so "replied to the weekly topic" can be detected.

`participation_config.json` (admin-tunable via `/set`) and `participation_messages.json`
(`/setmsg`) are committed back when a command changes them. `admin_chat_id` is deliberately
`null` in the committed file — the private admin group's id comes from the
`TELEGRAM_ADMIN_CHAT_ID` repo secret instead, so it stays out of this public repo
(`admin_chat_id()` in `participation.py`: env var > config > main chat).

The fallback button URL is `BADGE_APP_URL` (Actions *variable*, not secret) +
`/?type=<badge_type>`, defaulting to the GitHub Pages URL in `DEFAULT_BADGE_APP_URL`. When
a winner has a profile photo the button instead points at the FTP-uploaded avatar's URL
(`AVATAR_PUBLIC_BASE/<random>.png`, see `upload_avatar`).

**Badge avatar mini app** (`docs/`, served by GitHub Pages from `/docs`): a static
`index.html` that reads `?type=`, lets the winner upload a photo, composites it under a
badge ring on a `<canvas>`, and offers a download. It draws a coloured ring + emoji by
default; if `docs/frames/<type>.png` exists it's overlaid instead. No server, and **no
personal data** — the generated winner avatars are FTP'd to the subdomain, never here.

**Critical external setup** (see [AWARDS_GUIDE.md](AWARDS_GUIDE.md)): the bot's privacy
mode must be **disabled in @BotFather** (or bot made admin) or it sees no messages and all
counts are empty; repo is **public** for free unlimited Actions minutes + free Pages —
safe because every committed file is anonymous (numeric ids only, no names, no photos);
GitHub Pages must be enabled on `/docs`; and for personalised avatars, the FTP secrets
(`AVATAR_FTP_HOST` / `AVATAR_FTP_USER` / `AVATAR_FTP_PASSWORD` secrets, `AVATAR_PUBLIC_BASE`
+ optional `AVATAR_FTP_DIR` / `AVATAR_FTP_PORT` / `AVATAR_FTP_TLS` variables) must be set —
without them the avatar step is skipped and the button uses the upload page.

## Conventions

- Console output is reconfigured to UTF-8 at startup so emoji captions don't crash on
  Windows cp1252. Telegram payloads are always UTF-8 regardless.
- Never commit real tokens. `.env` is gitignored; `run_local.bat` ships with
  placeholders; GitHub uses repo secrets.
