# Participation & inactivity management

Tracks whether each member practises on **at least 3 different days a week**
(Monday–Sunday, UTC), nudges the ones who are behind, and runs a **two-strike**
system that can remove chronically inactive members.

> Built on the existing stack — Python + CSV committed to the repo + GitHub
> Actions cron. No server. All state is keyed by numeric Telegram id; **no names
> are stored**, so the repo stays safe to be public.

---

## The rule

A day counts as **active** if the member, that calendar day, sends one of:

| Counts | Bar |
|---|---|
| voice message | any |
| video message / video note | any |
| reply to the **weekly topic** post | ≥ 4 words (config: `reply_min_words`) |
| reply to **another member** | ≥ 4 words |

Does **not** count: standalone text (not a reply), stickers, GIFs, emoji-only,
reactions, `/commands`, forwarded media, and short greetings/filler
(`hi`, `thanks`, `test`, … — the `greeting_stoplist`).

Several messages on one day still count as **one** day. The classifier runs once,
when the message is logged, and writes a `practice` 0/1 column to
`activity_log.csv`. Message text itself is never stored.

## Strikes

| Event | Result |
|---|---|
| Finish a week with < 3 active days, no strike yet | **Strike 1** + a private warning |
| Finish a week with < 3 active days **while Strike 1 is active** | **Strike 2** → removal |
| Complete **4 consecutive successful weeks** after Strike 1 | Strike cleared |
| Any failed week | success-week counter resets to 0 |

`clear_weeks` (default 4) and `required_days` (default 3) are configurable.
Removal is a **kick** (ban + immediate unban) so the person can rejoin later.

## Reminders

- **Wednesday** — DM members at 0–1 active days.
- **Friday** — DM members still below 3, showing their progress.

Members who have never opened a private chat with the bot can't be DM'd; set
`dm_fallback_to_group: true` to @-mention them in the group instead, or leave it
off and they're just skipped (the weekly warning still lands).

## Pause / absence

`/pause [weeks] [reason]` — up to `pause.max_weeks` (default 2), **admin-approved**
by default. While paused: no counting, no reminders, no strikes. The pause ends
automatically. Abuse guard: `pause.max_per_window` approvals per
`pause.window_days`.

## New members

On join the bot posts a welcome with the rule, sets a **grace period** covering
the rest of the join week (plus `grace_extra_weeks`), and starts tracking from
their first full Monday–Sunday week.

---

## One-time setup

1. **@BotFather → privacy mode OFF** (already required by the awards feature) so
   the bot sees every message.
2. **Make the bot a group admin** with the **Ban users** right. Without admin it
   can't receive join/leave events or remove anyone.
3. Decide where admin output goes. Create a **private admin group**, add the bot,
   run `python get_chat_id.py` there (or check the poller logs), and set it:
   in `participation_config.json` set `"admin_chat_id": -100…`, or send
   `/set admin_chat_id -100…` from an admin. If left `null`, reports and pause
   requests go to the **main group**.
4. Add yourself as an admin for commands: either be a Telegram group admin, or
   `/addadmin <your_user_id>`.
5. Nothing else — the workflows (`log.yml`, `reminder-wed.yml`, `reminder-fri.yml`,
   `evaluate.yml`) use the existing `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`
   secrets. `members.csv` is auto-seeded from `activity_log.csv` on the first run.

## Recommended rollout

`auto_removal` ships **off** — Strike 2 members are only *flagged* in the report.

1. **Set the enforcement start** so the first partial week doesn't strike anyone:
   `/set tracking_starts <first Monday you want counted>` (e.g. `2026-09-14`).
   Weeks before that Monday are skipped by the evaluation.
2. **Announce & pin** the rule in the group; ask everyone to send one message
   (registers them) and to tap START on the bot (so reminders can reach them).
3. Let it run **2–3 weeks with `auto_removal` off**. Watch the Monday reports,
   tune `greeting_stoplist` / `reply_min_words`, use `/exempt` where needed.
4. When the numbers look right: `/set auto_removal on`.

Safety gates in `evaluate_participation.py` (a broken logger must not clear the
group): it aborts if a week has **no** logged practice activity, if far fewer
members than usual were active (unless `--force`), or if a run would remove more
than `max_removals_per_run` (default 5) people.

---

## Admin commands

Sent in the group or the admin chat by a group admin / `admin_user_ids` member.
Commands respond within ~10 min (poller cadence).

| Command | Does |
|---|---|
| `/report` | snapshot of this week's progress |
| `/member <id>` (or reply) | one member's full record |
| `/list below\|strikes\|paused\|grace\|exempt` | filtered list |
| `/strike add\|remove\|reset <id>` | adjust strikes |
| `/exempt <id> on\|off` | exempt a member from the rules |
| `/approve <id>` / `/reject <id> [reason]` | decide a pending pause |
| `/endpause <id>` | end a pause now |
| `/remove <id> [reason]` | manual kick (ban + unban) + logs it |
| `/set <key> <value>` | change a config value — `/settings` lists keys |
| `/setmsg <key> <text>` | edit a message template |
| `/settings` | dump current config |
| `/addadmin <id>` / `/deladmin <id>` | manage the admin allowlist |

Member commands: `/pause [weeks] [reason]`, `/mystatus`, `/rules`, `/help`.

Config lives in **`participation_config.json`**, message copy in
**`participation_messages.json`** — both editable directly on GitHub and
committed back when changed via commands.

## Testing without touching the group

```bash
python participation.py                              # classifier self-test
python evaluate_participation.py --dry-run           # full preview for last week
python evaluate_participation.py --dry-run --this-week
python send_reminders.py --which wed --dry-run
```

Every scheduled workflow's manual "Run workflow" button defaults to **Dry run =
true**.

## Data files (all committed, numeric-id only)

| File | Contents |
|---|---|
| `members.csv` | roster + strike/pause/grace state, keyed by `user_id` |
| `activity_log.csv` | existing log + new `practice` column |
| `weekly_results.csv` | one row per member per evaluated week (audit) |
| `pause_requests.csv` | every pause request + decision |
| `removals.csv` | removal history |
| `reminder_log.csv` | idempotency for Wed/Fri reminders |
| `participation_eval_log.csv` | idempotency for the weekly evaluation |
| `topic_posts.csv` | message ids of topic posts, for "replied to the topic" |

## Telegram limits that shape this

- **One `getUpdates` consumer** — all incoming handling is in `log_activity.py`;
  command latency ≈ the poll interval.
- **No bot-initiated DMs** — reminders/warnings only reach members who started
  the bot; removal proceeds regardless.
- **No member list API** — the roster is built from messages + join events;
  members who joined before the bot and never post stay invisible until they do.
- **Can't remove admins/owner**; the bot needs the Ban users right.
- **Rate limits** — bulk DMs are throttled ~1/sec and honour `retry_after`.
- Classifier changes are **forward-only** (text isn't stored, only the boolean).
