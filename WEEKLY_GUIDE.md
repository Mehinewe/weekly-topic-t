# How to add next week's topic (the easy way — no Excel, no git)

Do everything right on the **GitHub website**. This keeps your emojis intact
(Excel strips them) and needs no commands.

Your repo: https://github.com/Mehinewe/weekly-topic-telegram

---

## 1. Upload the image
1. Open the repo and click the **`images`** folder.
2. Click **Add file → Upload files**.
3. Drag in your new image (e.g. `7.jpg`).
4. Click **Commit changes**.

## 2. Add the schedule row
1. Back in the repo, click **`schedule.csv`**.
2. Click the **pencil ✏️** (top right) to edit.
3. Add one new line at the bottom — the **Monday date**, the **image filename**,
   and the **message** in quotes:
   ```
   2026-08-03,7.jpg,"📣 NEW WEEKLY THEME 📣
   Good morning everybody! ☀️
   Send videos to share what you think! 🎥"
   ```
4. Click **Commit changes**.

Done — the new topic posts automatically on its Monday. ✅

---

## Rules to remember
- **date** = the **Monday** the post should go out (format `YYYY-MM-DD`, e.g. `2026-08-03`).
- **image** = must **exactly match** the filename you uploaded (including `.jpg` / `.png`).
- **message** = wrap it in `"double quotes"`; you can use multiple lines and emojis.
- Posts go out **every Monday at 10:32 GMT** automatically — your PC can be off.

## Want to preview before Monday?
GitHub repo → **Actions** tab → **Weekly Telegram topic** → **Run workflow** →
leave **"Dry run" ticked** → it shows what *would* post, **without** sending to the group.

## Something went wrong?
- If a Monday post fails, GitHub emails you. Open **Actions**, click the red run,
  expand **"Post this week's topic"** to see the error.
- Common causes: image filename doesn't match the CSV, or the date isn't a valid Monday.
