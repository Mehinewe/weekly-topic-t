# Shadowing challenge (American Accent)

A weekly shadowing video, private entries by DM, and a private vote with a winner badge.
All times are **GMT**.

## The week

| When (GMT) | What happens | Script / workflow |
|---|---|---|
| Monday 10:47 | Bot posts the weekly shadowing video in the group | `send_shadowing.py --phase video` |
| Mon – Thu | Members DM their shadowing video to the bot (a newer one replaces the older) | poller, `log_activity.py` |
| Thursday 12:00 | Announced deadline for entries | (guidance only) |
| Thursday 15:47 | Entries are posted as **Video 1, 2, 3…** (anonymous, shuffled) and each member gets a private ballot | `--phase showcase` |
| Thu – Sun | Members tap a button in their ballot to vote; they can change it until close | poller |
| Sunday 15:47 | Voting closes; top places announced, winner's video re-shown, winner badge posted | `--phase results` |

Each phase has two later catch-up cron slots (+2 h, +4 h) and is idempotent, so a late or dropped GitHub run is safe.
The real cut-off for entries is the moment the Thursday showcase starts, not 12:00.

## Weekly content

1. Put the video in `videos_shadowing/` (.mp4/.mov/.m4v/.webm, **max 50 MB**; for bigger files use `file_id:<telegram file id>` in the CSV instead).
2. Add a row to `schedule_shadowing.csv`: `date,video,message` (any date in the Monday's week; the caption goes in `message`, quote it).
3. Commit. Preview any time: `python send_shadowing.py --phase video --date 2026-10-05 --dry-run`.

## Members

- **Join link:** `https://t.me/Merv_english_bot?start=join` (print it any time with `python send_shadowing.py --join-link`). Tapping it opens a private chat with the bot; pressing **Start** joins the challenge *and* opens the DM channel for entries and ballots in one step. The Monday video post also carries a "🎤 Join the challenge" button with this link. Only current group members can join this way.
- `/join_challenge` (group or DM) — the typed alternative. Only members can submit or vote. `/leave_challenge` opts out.
- They must open a private chat with the bot and press **Start**; the bot can't DM first. Anyone whose ballot can't be delivered is @-mentioned in the group after the showcase and can send `/vote` to the bot to get it.
- `/vote` (DM) re-sends the ballot; `/vote 3` votes directly. `/myentry` says whether their entry was received.
- Voting is private, one vote each, no self-votes (their own video isn't a button).
- A DM'd shadowing video counts as a **practice day** for the ≥ 3 days/week rule (activity type `shadowing`, so it does *not* count toward Video Shark).
- Confirmations can take up to ~10 minutes: the poller runs on a `*/10` cron.

## Admin

- `/set shadowing_ranking_size 3` — how many places Sunday announces (1 = winner only; default 1). Decide after sign-ups close.
- `/set shadowing_enabled off` — pause the whole feature.
- Ties are broken by earliest submission; the announcement says so when it decided a place. Entries with 0 votes never place; no votes at all → "no votes" post.
- Fewer than 2 entries → no vote that week (a "no entries" / "only one entry" post instead).
- Wording lives in `shadowing_messages.json` (keep it saying GMT).
- Winner badge art: `badges/shadowing_champion.png` (currently a simple placeholder — replace with your artwork, same filename). The button uses the badge-avatar mini app type `shadow` (`docs/index.html`); with a profile photo the winner also gets the flip GIF + hosted avatar, exactly like the weekly awards.

## State (committed, numeric ids only — no names, no video files)

`shadowing_participants.csv`, `shadowing_entries.csv` (Telegram `file_id`s), `shadowing_votes.csv` — written by the poller.
`shadowing_state.json` — per-week progress (numbering, what has been sent) — written only by `send_shadowing.py`, which lets a failed run resume without double-posting.

## Manual run

Actions → **Shadowing challenge** → Run workflow: pick phase, optional date, leave *Dry run* ticked to preview.

## One-time setup

- The bot must receive `callback_query` updates (the poller now requests them) and private messages — no BotFather change needed beyond the existing privacy-mode setting.
- Announce `/join_challenge` and "press Start on the bot" in the group before the first Monday.
