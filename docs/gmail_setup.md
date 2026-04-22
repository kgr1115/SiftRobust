# Gmail Setup Walkthrough

One-time setup to let SiftRobust talk to your Gmail account via the Gmail API. Takes about 10 minutes.

SiftRobust requests a single scope: `gmail.modify`. That one scope covers everything the app needs — reading threads, applying labels, archiving, drafting replies, and sending. It does **not** include permanent deletion. The app never auto-sends on your behalf (drafts always land in your Drafts folder for review), and bulk archive/label actions are guarded by a dry-run default plus a safe-category whitelist (see `docs/design_decisions.md`).

---

## 1. Create a Google Cloud project

1. Go to <https://console.cloud.google.com/> and sign in with your Google account.
2. In the top bar, click the project picker → **New Project**.
3. Name it something like `sift-robust`. No organization. Click **Create**.
4. Wait for it to provision, then select it in the project picker.

## 2. Enable the Gmail API

1. From the left nav: **APIs & Services → Library**.
2. Search for **Gmail API**. Click it → **Enable**.

## 3. Configure the OAuth consent screen

Because this is a personal app running on your machine, you can keep it in **Testing** mode forever — no verification required.

1. Left nav: **APIs & Services → OAuth consent screen**.
2. User Type: **External**. Click **Create**.
3. **App information:**
   - App name: `SiftRobust` (or whatever you like)
   - User support email: your address
   - Developer contact email: your address
4. **Scopes:** skip for now — the app requests them programmatically on first run. Click **Save and Continue**.
5. **Test users:** add your own Google email. Click **Save and Continue**.
6. **Summary:** click **Back to Dashboard**.

## 4. Create an OAuth client

1. Left nav: **APIs & Services → Credentials**.
2. Click **Create Credentials → OAuth client ID**.
3. Application type: **Desktop app**.
4. Name: `SiftRobust desktop`. Click **Create**.
5. Click **Download JSON** on the resulting credential.
6. Save it as `credentials.json` in the root of this repo — `C:\Projects\SiftRobust\credentials.json` — the same folder as `pyproject.toml`.

> **`credentials.json` and `token.json` are both in `.gitignore`.** Do not commit them.

## 5. First run — authorize the app

From an activated venv, run:

```powershell
sift auth
```

Your browser opens to Google's OAuth screen. Sign in with the same Google account you added as a test user, review the requested scope (you'll see **Read, compose, send, and permanently delete all your email from Gmail** — that's `gmail.modify`; SiftRobust does not call the delete endpoint), and click **Allow**.

The flow writes `token.json` next to `credentials.json`. Subsequent runs reuse the token silently until it expires.

After that, refresh the web UI and the inbox call will succeed.

### Alternative: trigger auth through the web UI

If you skip `sift auth`, the first API call the web UI makes (e.g. loading the Inbox tab) will try to run the OAuth flow server-side. That works too, but doing `sift auth` explicitly from the command line is cleaner — you see the browser prompt in a predictable moment instead of while the UI is loading.

---

## Troubleshooting

**"credentials.json not found" in the web UI.**
You haven't done step 4 yet, or the file landed somewhere other than the repo root. The file must be at exactly `C:\Projects\SiftRobust\credentials.json`.

**"This app isn't verified" warning on the OAuth screen.**
Expected. Because the app is in Testing mode and you're the only test user, Google flags it. Click **Advanced → Go to SiftRobust (unsafe)**. It's your own app running on your machine.

**`invalid_grant` when running later.**
Your refresh token likely expired (Testing-mode tokens expire after 7 days). Run `sift auth --force` to re-authenticate, or delete `token.json` and retry any Gmail command.

**Rate limits.**
The Gmail API gives personal accounts a generous quota. If you hit limits, lower the thread limit in the UI dropdown or pass `--limit` to CLI commands.

**Want to revoke access.**
Go to <https://myaccount.google.com/permissions> and remove "SiftRobust".
