# Publishing checklist

Delete this file after the first push — it's a one-time scratch pad.

## 1. Rotate API keys first (IMPORTANT)

Your Anthropic, OpenAI, Google, and Groq API keys passed through a Claude conversation during the build/debug process. Rotate them before pushing, and regenerate them in each provider's console:

- Anthropic: https://console.anthropic.com/settings/keys
- OpenAI: https://platform.openai.com/api-keys
- Google (Gemini): https://aistudio.google.com/apikey
- Groq: https://console.groq.com/keys

Then drop the new keys into `.env`. `.env` is gitignored, so nothing sensitive ends up in the repo.

## 2. Clean up the partial .git directory (sandbox artifact)

A failed `git init` left a broken `.git` folder. From PowerShell:

```powershell
cd C:\Projects\SiftRobust
Remove-Item -Recurse -Force .git
```

## 3. Initialize git + first commit

```powershell
cd C:\Projects\SiftRobust
git init -b main
git config user.name  "Kyle Rauch"
git config user.email "kyle.g.rauch@gmail.com"
git add -A
git status                    # sanity-check — no .env, token.json, credentials.json, sift.db, logs/
git commit -m "Initial commit: SiftRobust — AI inbox triage with safety-gated actions"
```

## 4. Push to GitHub

Create a new repo at https://github.com/new (name: `SiftRobust`, public, **no** auto-generated README/license/gitignore since we already have them).

```powershell
git remote add origin https://github.com/<your-username>/SiftRobust.git
git push -u origin main
```

## 5. Double-check the live repo

After push:

- [ ] `.env`, `token.json`, `credentials.json`, `sift.db`, `logs/` are **not** visible on GitHub.
- [ ] `README.md` renders correctly (the eval scorecard table, project layout, etc.).
- [ ] `evals/last_provider_comparison.md` renders.
- [ ] GitHub detected Python as the primary language (it will — ~4.4k lines of Python).

## 6. Optional polish before announcing

- Add a real `LICENSE` file (MIT template: https://choosealicense.com/licenses/mit/).
- Add a screenshot or GIF to the README (drag-drop into a GitHub issue to upload, then grab the URL).
- Tag a `v0.1.0` release: `git tag v0.1.0 && git push --tags`.
- Turn on GitHub Pages for `/docs` if you want `docs/design_decisions.md` on the public web.

Delete this file (`PUBLISH.md`) before the first commit, or commit with it and delete in a second commit — either's fine.
