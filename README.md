# Weekly Telegram Topic Auto-Poster

Posts a weekly English **topic** — a PNG image + a message — to your Telegram group
**automatically every Monday morning**. You prepare the content ahead of time; the bot
picks each week's entry and posts it.

## How it works
- `schedule.csv` lists one row per Monday: `date,image,message`.
- `images/` holds the PNG files referenced by that CSV.
- `send_weekly_topic.py` picks the row for the current week and posts it via the
  Telegram Bot API (`sendPhoto`, with the message as the caption).
- A scheduler runs the script every Monday — **GitHub Actions** (recommended, always-on,
  free) or **Windows Task Scheduler** (`run_local.bat`, needs your PC on).

## One-time setup

### 1. Create the bot
1. In Telegram, message **@BotFather** → `/newbot` → pick a name + username.
2. Copy the **bot token** it gives you (e.g. `123456:ABC-DEF...`). Keep it secret.

### 2. Add the bot to your group
- Add the bot to your English group and **promote it to admin** so it can post.

### 3. Find your group chat id
```
set TELEGRAM_BOT_TOKEN=your_token_here     (Windows CMD)
python get_chat_id.py
```
Send a message in the group first so it shows up. Copy the **negative** id
(e.g. `-1001234567890`).

### 4. Add your content
- Drop your PNGs into `images/`.
- Edit `schedule.csv`: one row per Monday with the date (`YYYY-MM-DD`), the image
  filename, and the message. Wrap messages containing commas in `"double quotes"`.

## Test it locally
```
pip install -r requirements.txt
set TELEGRAM_BOT_TOKEN=your_token_here
set TELEGRAM_CHAT_ID=-1001234567890
python send_weekly_topic.py 2026-06-22      # force a specific Monday for testing
```
The image + message should appear in your group.

## Schedule it

### Option A — GitHub Actions (recommended)
1. Push this folder to a (private) GitHub repo.
2. Repo **Settings → Secrets and variables → Actions** → add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. The workflow `.github/workflows/weekly.yml` runs every Monday. **Cron is in UTC** —
   edit the hour for your local time (8 AM Denver = 14:00 UTC in summer, 15:00 UTC in
   winter; see comments in the file).
4. Test without waiting for Monday: **Actions** tab → *Weekly Telegram topic* →
   **Run workflow**.

### Option B — Windows Task Scheduler (local)
1. Edit `run_local.bat` and add your token + chat id (or set them as Windows env vars).
2. Task Scheduler → Create Task → Trigger: **Weekly, Monday, 8:00 AM** → Action: start
   `run_local.bat`. Only runs when your PC is on and awake.

## Weekly routine
Each week you only: drop the new PNG in `images/`, add a row to `schedule.csv`
(and, for GitHub Actions, commit/push). That's it.

## Notes
- Telegram caption limit is 1024 chars; longer messages are auto-split (photo + follow-up text).
- Never commit your real token. `.env` is gitignored; use repo secrets for GitHub Actions.
