# Wednesday idiom challenges

Wednesday now has two posts: an illustrated guessing challenge at **10:32 UTC**
and an answer at **18:32 UTC**, with a meaning, two examples, and a speaking prompt.
The answer replies to the morning picture. Members can guess in text or by voice;
the existing speaking participation rules still apply. The reveal is prepared
content, not an automatic review or score of members' guesses.

## Content and dates

Edit `idioms_wednesday.json`. Each entry needs:

- `date`: a unique Wednesday in `YYYY-MM-DD` format.
- `image`: a PNG/JPG filename inside `images_idioms/`.
- `idiom`, `hint`, and `meaning`: the expression, a clue, and its explanation.
- `examples`: exactly two natural example sentences.
- `speaking_prompt`: a question or situation encouraging members to use the idiom.

The first four weeks are ready: September 16, 23, 30, and October 7, 2026.
**Add the October 14 entry and subsequent weeks before they arrive.** Missing dates
fail visibly in Actions; the bot never silently reuses an old idiom.
Duplicate a complete JSON object, change its fields, and upload its illustration.
Use clear literal visual clues without printing the answer on the image. Preview
both posts before committing; keep the challenge caption under 1,024 characters.

## Preview locally

```bash
python -m pip install -r requirements.txt
python send_wednesday_idiom.py --phase challenge --date 2026-09-16 --dry-run
python send_wednesday_idiom.py --phase reveal --date 2026-09-16 --dry-run
python -m unittest discover -s tests -v
```

Previews require no credentials, contact no services, and write no state. A reveal
can be previewed before the morning post exists. Real sends use the existing
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` secrets or gitignored `.env`.

## Scheduling and recovery

`.github/workflows/wednesday.yml` replaces the old picture-description job.
Challenge catch-ups run at 12:47 and 14:47 UTC; reveal catch-ups at 20:47 and
22:47 UTC. Times are UTC year-round. Delayed scheduled challenges skip after
18:32 UTC, and all scheduled posts skip outside Wednesday. In Actions, choose
**Wednesday idiom challenge**, select a phase/date, and leave **Dry run** checked
to preview. Unchecking it sends a real post; manual runs bypass time windows.

Keep `idiom_sent_log.json` committed. It records each phase's message ID and saves
the morning post's matching answer, so midday content edits cannot change that
evening's explanation. A reveal requires a recorded challenge. If the original
picture is deleted, its reply can fail; inspect the chat before retrying.

Successful posts are skipped on retries. Telegram sending and Git persistence
are separate operations: a timeout or failed state push can still leave an
unrecorded post. Inspect the chat and restore the state before rerunning in that
case. Never clear this log simply to retry a job.

The retired `schedule_wednesday.csv` and `images_wednesday/` remain as reference
assets; no scheduled workflow uses them. Monday topics and participation reminders
retain their existing schedules.
