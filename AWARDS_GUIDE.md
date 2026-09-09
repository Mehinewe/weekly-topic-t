# Weekly Awards — setup & how it works

Every Monday this posts award GIFs to the group celebrating last week's top
contributors (🦈 Video Shark, 🎙️ Voice Legend, 🦋 Social Butterfly), each with a
button that opens a page where the winner makes a profile picture with their
badge.

Winners are **computed automatically** from what people actually posted. That
needs the bot to watch the group all week, so there are two moving parts:

- **Logger** (`log_activity.py`) — runs every hour, records who posted what.
- **Poster** (`send_weekly_awards.py`) — runs Monday, tallies last week, posts.

---

## One-time setup

### 1. Let the bot see every message
By default a Telegram bot only sees messages that mention it, so it can't count
activity. Fix this once:

- In Telegram, message **@BotFather** → `/setprivacy` → pick your bot → **Disable**.
- (Alternatively, make the bot a group **admin** — admins see all messages.)

Without this the counts will be empty or wrong.

### 2. Keep the repo public
The repo is public on purpose — a public repo gets **unlimited free GitHub
Actions minutes** (this project runs a lot of cron) and **free GitHub Pages**.
A private repo caps both behind a paid plan.

This is safe: every committed file holds **only anonymous numeric ids** — no
names, no photos. Winners' names are looked up live from Telegram at post time,
and the personalised avatar (which contains a face) is uploaded by FTP to a
subdomain you control, never committed here. See step 6.

### 3. Turn on the badge avatar page (GitHub Pages)
- **Settings → Pages → Build and deployment → Source: Deploy from a branch.**
- Branch: `main`, folder: **`/docs`** → Save.
- After a minute your page is live at
  `https://<you>.github.io/<repo>/` (for this repo:
  `https://mehinewe.github.io/weekly-topic-telegram/`).
- If your URL differs, set it so the buttons point to the right place:
  **Settings → Secrets and variables → Actions → Variables → New variable**
  named `BADGE_APP_URL` with your Pages URL.

### 4. Add the award badges
Drop one image per award into **`badges/`**, named to match `awards.csv`:
`video_shark.png`, `voice_legend.png`, `social_butterfly.png` (animations
`.gif`/`.mp4` also work). Until they're there, the Monday run skips that award
with a warning.

**Personalised flip:** when posting, the bot fetches the winner's Telegram
profile photo and builds a short animated GIF that flips the badge over to
reveal their photo in the circle (like the original). If a member has no
profile photo, or it's hidden from the bot, that award just posts the static
badge instead — nothing breaks.

### 5. The "Get Your Badge Avatar" button
For each winner the poster also builds a **badge avatar** — their profile photo
with the award emblem in the corner. Because that image contains a face it is
**never written to this public repo**: it's built to a temp file, uploaded by
FTP to your subdomain (step 6) under a random unguessable name, and the button
links straight to that image. The temp file is deleted after each post.

If FTP isn't configured, or a member has no profile photo (or it's hidden from
the bot), the button instead opens the manual page where they upload a photo
themselves. That page draws a coloured ring + emoji by default; to give it
custom art, add transparent square PNGs to **`docs/frames/`**: `shark.png`,
`microphone.png`, `butterfly.png`.

### 6. Avatar hosting — an FTP subdomain
The generated avatars need to live *somewhere* the winner's browser can open,
but *not* in the repo. Point a subdomain at a web folder on your Hostinger
hosting and the poster uploads there over FTP (a few MB per week, at most).

1. **Create the subdomain.** hPanel → *(your site)* → **Domains → Subdomains**
   → create e.g. `badges.yourdomain.com`. If the domain's nameservers are
   external (e.g. Vercel/Cloudflare — Hostinger warns you), also add a DNS
   **A record** for the subdomain at that DNS provider, pointing at the
   Hostinger **"Website IP address"** (Plan details). Wait for it to resolve,
   then Hostinger auto-issues the SSL cert (hPanel → SSL; up to a few hours).
   Done when `https://badges.yourdomain.com/` loads without a warning.
2. **Create a dedicated FTP account.** hPanel → **Files → FTP Accounts →
   Create FTP account**. Username letters/numbers only (no `.`/`@`); copy the
   full login it generates. A dedicated account keeps this isolated from the
   main hosting account.
3. **Find the upload folder.** Log in with an FTP client and look at the tree.
   Hostinger often drops the login into the *parent* domain's `public_html`
   with the subdomain nested one level down (`<sub>/…`), so the target ends up
   like `<sub>/av` (`av` = a subfolder the script creates). `AVATAR_FTP_DIR` is
   that path from where the FTP login lands.
4. In GitHub → **Settings → Secrets and variables → Actions**:

   **Secrets:**
   - `AVATAR_FTP_HOST` — the Hostinger server IP ("Website IP address" / "FTP
     IP" in Plan details)
   - `AVATAR_FTP_USER` — the full FTP login from step 2
   - `AVATAR_FTP_PASSWORD`

   **Variables:**
   - `AVATAR_PUBLIC_BASE` — the public URL that folder is served at, e.g.
     `https://badges.yourdomain.com/av` (no trailing slash)
   - `AVATAR_FTP_DIR` — the path from step 3, e.g. `badges/av`
   - `AVATAR_FTP_TLS` — use `noverify` for Hostinger: its FTPS cert is for the
     shared-server hostname, not your IP, so strict verification fails;
     `noverify` keeps the channel encrypted but skips the hostname check.
     (`true` = verify, `false` = plain FTP.)
   - `AVATAR_FTP_PORT` — *only if not 21*

Without the FTP secrets the poster still runs — it just skips the avatar
upload and the button falls back to the manual page.

The same `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` repo secrets used by the
weekly topic poster are reused here. If your **admin group** for reports/pause
approvals is a *different* chat from the main group, also add a
`TELEGRAM_ADMIN_CHAT_ID` secret with that chat's id (it used to sit in
`participation_config.json`; it was moved to a secret so the public repo doesn't
expose it).

---

## How winners are decided
Computed from `activity_log.csv` (anonymous ids only) for the **previous** week
(Mon–Sun); the winner's name is fetched live from Telegram when posting:

| Award | Metric |
|---|---|
| 🦈 Video Shark | most `video` + video-bubble messages |
| 🎙️ Voice Legend | most `voice` messages |
| 🦋 Social Butterfly | most **replies to other people** (falls back to most messages if nobody replied) |

Ties break deterministically; an award with no activity is silently skipped.
Counting only starts once the logger is live — **the first week may be thin**,
since Telegram gives the bot no history.

---

## Editing the award text
Edit **`awards.csv`** (columns `key, metric, gif, message, badge_type`). Use
`{name}` where the winner's name should go; wrap the message in `"double quotes"`;
multiple lines and emoji are fine. `badge_type` must match a key in the avatar
page (`shark`, `microphone`, `butterfly`).

## Preview before it posts
GitHub → **Actions** → **Weekly awards** → **Run workflow** → leave **Dry run**
ticked. It prints who *would* win without posting. Locally:

```
python send_weekly_awards.py --dry-run            # last week
python send_weekly_awards.py 2026-06-22 --dry-run # pretend today is that Monday
```

## Troubleshooting
- **Everyone has 0 activity** → bot privacy mode is still on (step 1), or the
  logger hasn't been running. Check the **Log group activity** workflow runs.
- **An award didn't post** → its GIF is missing from `badges/`, or nobody did
  that activity last week. The Monday run log says which.
- **Button opens the wrong page** → set the `BADGE_APP_URL` Actions variable
  (step 3).
- **Button always opens the manual upload page even for winners with a photo**
  → the `AVATAR_FTP_*` secrets/vars aren't set, or the upload failed. The Monday
  run log prints `avatar FTP upload failed: …`:
  - `530 Login incorrect` → wrong `AVATAR_FTP_USER` / `AVATAR_FTP_PASSWORD`.
  - `getaddrinfo failed` / timeout → wrong `AVATAR_FTP_HOST` or port.
  - `CERTIFICATE_VERIFY_FAILED` / `IP address mismatch` → set `AVATAR_FTP_TLS`
    to `noverify` (encrypted, skips the cert hostname check — needed for
    Hostinger). `425 … TLS session … not resumed` → same fix; last resort
    `false` for plain FTP.
  - `550` on the STOR/CWD → `AVATAR_FTP_DIR` points somewhere the account
    can't write; check the path is relative to the FTP home.
- **Button opens a URL that 404s** → `AVATAR_PUBLIC_BASE` doesn't match where
  `AVATAR_FTP_DIR` actually is on disk, or the subdomain's web root differs from
  the FTP path. Upload one file by hand with an FTP client and find its real
  public URL.
