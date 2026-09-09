# Project Plan — Weekly Telegram Topic Auto-Poster

## 1. Overview
An automation that posts a weekly English-learning **topic** — a **PNG image** plus a
**text message** — to a **Telegram group**, **automatically every Monday morning** at a
fixed hour. Content is prepared ahead of time; the system picks the right entry each week
and posts it with no manual action.

- **Owner:** Mehinewe
- **Status:** Built — pending Telegram credentials and first live test
- **Repository folder:** `Subject automation/`

## 2. Goals & non-goals
**Goals**
- Post one topic (image + message) to the Telegram group every Monday at a set time.
- Let the owner queue content weeks ahead in a simple, editable file.
- Run reliably without the owner's PC needing to be on (cloud option).
- Keep secrets (bot token) out of the codebase.

**Non-goals (for now)**
- AI-generating the weekly content (content is pre-made).
- Two-way bot interaction / replies / quizzes.
- Multiple groups or multiple posts per week.

## 3. Requirements
| # | Requirement |
|---|-------------|
| R1 | Send a PNG image + message to a specific Telegram group. |
| R2 | Trigger automatically every Monday at a configurable hour. |
| R3 | Select the correct topic for the current week from a prepared list. |
| R4 | Tolerate a missed week by re-sending the most recent topic (no silent gaps). |
| R5 | Handle messages longer than Telegram's 1024-char caption limit. |
| R6 | Keep the bot token / chat id secret (env vars / repo secrets). |
| R7 | Fail loudly (non-zero exit) so a failed run is visible in logs. |

## 4. Architecture
```
                 Monday, scheduled hour
                          │
        ┌─────────────────┴──────────────────┐
        │  Scheduler                          │
        │  • GitHub Actions cron (recommended)│
        │  • or Windows Task Scheduler (.bat) │
        └─────────────────┬──────────────────┘
                          │ runs
                          ▼
              send_weekly_topic.py
        ┌──────────────────────────────────┐
        │ 1. Read env: TOKEN, CHAT_ID       │
        │ 2. Load schedule.csv              │
        │ 3. Pick this week's row (Monday)  │
        │ 4. sendPhoto(image, caption)      │
        │    + sendMessage if text > 1024   │
        └──────────────────┬───────────────┘
                          │ HTTPS
                          ▼
                 Telegram Bot API  ──►  Your group
```

**Content model** — `schedule.csv`, one row per Monday:
`date (YYYY-MM-DD), image (filename in images/), message (caption text)`.

## 5. Components / file inventory
| File | Role |
|------|------|
| `send_weekly_topic.py` | Main script: select week's row, post image + caption to Telegram. |
| `get_chat_id.py` | One-time helper to discover the group's chat id via `getUpdates`. |
| `schedule.csv` | Editable weekly content manifest (`date,image,message`). |
| `images/` | PNG files referenced by the schedule. |
| `requirements.txt` | Python dependency (`requests`). |
| `.github/workflows/weekly.yml` | GitHub Actions cron + manual-trigger workflow. |
| `run_local.bat` | Windows Task Scheduler entry point (local alternative). |
| `.env.example` | Template for the two secrets; copy to `.env` for local use. |
| `.gitignore` | Keeps `.env` and Python cruft out of git. |
| `README.md` | Step-by-step setup & run guide. |
| `PROJECT_PLAN.md` | This document. |

## 6. Hosting decision
**Recommended: GitHub Actions** — free, always-on, runs even when the PC is off; secrets
stored as encrypted repo secrets; supports manual test runs (`workflow_dispatch`).

**Alternative: Windows Task Scheduler** (`run_local.bat`) — simplest to set up, but only
fires when the PC is on and awake at the scheduled time.

## 7. Scheduling & time zones
- GitHub cron is **UTC**. Denver is Mountain Time:
  - Summer (MDT, UTC−6): 8:00 AM Denver = **14:00 UTC**
  - Winter (MST, UTC−7): 8:00 AM Denver = **15:00 UTC**
- Default set to `0 14 * * 1` (Mondays 14:00 UTC). A 1-hour drift occurs across daylight-
  saving changes; swap to `0 15 * * 1` in winter if exact 8 AM is required.
- GitHub scheduled jobs can be delayed a few minutes under load — acceptable for a weekly post.

## 8. Security
- Bot token + chat id are **never** hard-coded: read from environment variables locally and
  from **GitHub repo secrets** in CI.
- `.env` is gitignored. Recommend a **private** repo since `images/` and content live in it.

## 9. Setup checklist (one-time)
1. Create bot via **@BotFather**, copy token.
2. Add bot to the group, promote to **admin**.
3. Run `get_chat_id.py` to obtain the negative group chat id.
4. Add PNGs to `images/` and rows to `schedule.csv`.
5. Local test run, then either push to GitHub (+ add secrets) or set up Task Scheduler.

## 10. Weekly operating routine
Each week: drop the new PNG into `images/`, add a dated row to `schedule.csv`, and (for the
GitHub Actions path) commit + push. Nothing else.

## 11. Verification / acceptance
| Test | Expected |
|------|----------|
| `python send_weekly_topic.py 2026-06-22` | Image + caption appear in the group. |
| `python get_chat_id.py` | Prints the group's negative chat id. |
| Schedule logic (verified) | Exact-date match works; missing week falls back to most recent. |
| GitHub **Run workflow** | Green run; post arrives without waiting for Monday. |
| Time-zone check | Cron UTC hour maps to intended Denver local hour. |

## 12. Open items
- Confirm exact **local posting time** (and adjust the cron accordingly).
- Confirm **hosting choice**: GitHub Actions (free repo) vs. local Windows Task Scheduler.

## 13. Possible future enhancements
- AI-generated weekly topics (auto-create message and/or render the PNG).
- Auto-render images from a template so only text needs editing.
- Pin the weekly post or schedule reminders later in the week.
- Track which topics were sent (a `sent` column or log) to fully automate ordering.
- Support multiple groups or languages.
