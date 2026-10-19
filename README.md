# BMVS Darwin → Telegram (free GitHub Actions version)

This checker runs in GitHub's cloud every ~5 minutes, looks for **Darwin, NT** BMVS appointment availability, and sends a Telegram message if it sees an available date/time.

It is **notification-only**. It does not book/reserve an appointment and does not bypass CAPTCHA/security checks.

## Important before you start

- GitHub scheduled workflows have a **5-minute minimum schedule**, and GitHub may occasionally delay a scheduled run.
- To keep this completely free for frequent checks, use a **public GitHub repository** with standard GitHub-hosted runners. **Never put your Telegram token or personal details in the files**; add them only as GitHub Secrets.
- The checker defaults to exam codes **501,502** because those are common. If your referral letter lists different examinations, set `BMVS_EXAM_CODES` to your actual codes.
- BMVS can change its website at any time. If Bupa adds CAPTCHA or changes the booking flow, the bot will alert you instead of bypassing it.
- Because each GitHub run is separate, an available slot may trigger another Telegram alert on the next 5-minute run while it remains available. This is intentional so a slot is hard to miss.

## Setup — easiest path

### 1) Create your Telegram bot

1. In Telegram, open **@BotFather**.
2. Send `/newbot` and follow the prompts.
3. Copy the bot token BotFather gives you.
4. Open your new bot and send it `/start` once.

You do **not** need to find your Telegram chat ID manually. The first workflow run will discover it from your `/start` message. If you prefer, you can add `TELEGRAM_CHAT_ID` as a secret too.

### 2) Create a GitHub repository

1. Sign in to GitHub.
2. Create a **new public repository** named something like `bmvs-darwin-monitor`.
3. Upload all files from this folder, including the hidden `.github` folder.

### 3) Add the Telegram token as a GitHub Secret

In your repository:

**Settings → Secrets and variables → Actions → New repository secret**

Add:

- Name: `TELEGRAM_BOT_TOKEN`
- Value: the token from BotFather

Optional: add `TELEGRAM_CHAT_ID` if you already know it. Otherwise leave it out and the script will use the `/start` message you sent.

### 4) Set your examination codes

Go to:

**Settings → Secrets and variables → Actions → Variables → New repository variable**

Add:

- Name: `BMVS_EXAM_CODES`
- Value: for example `501,502`

Use the exact examination codes from your referral letter.

### 5) Test it once

Open:

**Actions → Check BMVS Darwin → Run workflow**

If the Bupa flow lets the checker reach the calendar without personal details, that's all you need. The scheduled checks will then run automatically.

## If Telegram says Bupa needs personal details first

Some BMVS flows may ask for identifying details before the calendar. Only if the bot tells you this is necessary, add the relevant values as **GitHub Secrets** (not repository variables and never directly in the code):

- `BUPA_HAP_ID`
- `BUPA_FIRST_NAME`
- `BUPA_LAST_NAME`
- `BUPA_DOB` — use the format Bupa shows, usually `DD/MM/YYYY`
- `BUPA_PASSPORT`

Then run the workflow manually again.

## What an alert looks like

`🚨 BMVS DARWIN SLOT FOUND`

The message includes the date/time candidates detected and tells you to open Bupa yourself to verify and book.

## Changing the location

This package is preconfigured for **Darwin, NT / postcode 0800**. The workflow passes these values to the script on every run.

## Stop the checker

Open the repository's **Actions** tab and disable the workflow, or delete `.github/workflows/check.yml`.

## Privacy

A public repository is public, but **GitHub Secrets are not stored in the repository files**. Do not print, commit, paste, or screenshot your secret values into issues, README files, workflow logs, or source files.
