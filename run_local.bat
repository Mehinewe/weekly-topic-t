@echo off
REM ---------------------------------------------------------------
REM Local runner for the weekly Telegram topic (Windows alternative
REM to GitHub Actions). Point Windows Task Scheduler at this file to
REM run it every Monday morning. Your PC must be ON and awake then.
REM
REM SECURITY: fill in your real token/chat id below. Keep this file
REM private (it is gitignored via *.bat? -- no: edit .gitignore if you
REM store secrets here, or better, set them as Windows user env vars).
REM ---------------------------------------------------------------

cd /d "%~dp0"

REM Option A: set secrets here (simple, but keep this file private)
set "TELEGRAM_BOT_TOKEN=PUT_YOUR_TOKEN_HERE"
set "TELEGRAM_CHAT_ID=PUT_YOUR_CHAT_ID_HERE"

REM Option B (safer): comment out the two lines above and instead set
REM TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as Windows user environment
REM variables (Settings > System > About > Advanced system settings >
REM Environment Variables). Then this script uses those automatically.

python send_weekly_topic.py
