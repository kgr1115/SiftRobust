# Design Decisions — SiftRobust

This is the "why" behind the robust-version extensions. The baseline Sift's design decisions (classifier architecture, evals-as-the-product, drafts-only, prompt files, etc.) are in [`../../SwiftCopilot/AI Utilization Project/docs/design_decisions.md`](../../SwiftCopilot/AI%20Utilization%20Project/docs/design_decisions.md) and still apply here. This doc covers the new calls I made for the robust version.

---

## 1. Copy-and-extend, not depend-and-import

**Decision:** SiftRobust is a full copy of the Sift Python package, then extended in place. It does not import from the baseline repo.

**Why:** The baseline is my "done" portfolio artifact — I want it to stay frozen at a point in time that a recruiter can clone and reproduce. SiftRobust is an evolution; bundling them as two separate repos means the baseline keeps its clean "here's the week-long build" story while the robust version gets room to grow without breaking the original.

**Trade-off:** Two places to fix a prompt bug. I accept that — the prompts are small and rarely change post-ship.

## 2. `gmail.modify` scope (one token, one consent prompt)

**Decision:** The OAuth scope is a single `https://www.googleapis.com/auth/gmail.modify`. Baseline Sift used `gmail.readonly + gmail.compose`.

**Why:** `gmail.modify` is a superset of both and also grants label/trash/archive/mark-read — exactly what the new action layer needs. Using one scope means one consent screen, one token refresh surface, and one thing to explain when someone asks "what does the app touch?". Google's doc explicitly recommends the smallest scope that fits the use case; `gmail.modify` is still smaller than the full `mail.google.com` scope that would let the app delete threads permanently.

**Trade-off:** Existing Sift users need to re-consent when they upgrade. `sift auth --force` handles it.

## 3. `ActionPolicy` as declarative config, not an imperative API

**Decision:** The bulk-action surface takes a `Pydantic` `ActionPolicy` object and returns an `ApplyReport`. There's no "run archive on these threads" method at the top of the public API.

**Why:** Declarative policies are previewable. `ActionPolicy(dry_run=True)` generates the same `ApplyReport` as the non-dry run, minus the side effects, which means the UI can show a side-by-side "here's what would happen" diff before the user commits. Imperative code can't do that without building a shadow interpreter.

**Corollary:** The same `ActionPolicy` object is the thing the UI's policy-builder produces, the thing the CLI's `sift apply` constructs from flags, and the thing the eval harness would assert on. One shape to test.

## 4. Three-layer safety: dry-run default → confidence floor → safe-category whitelist

**Decision:** The action layer gates every write through three checks in order: is the policy a dry run, is the classifier confidence above the floor, and does the category belong to `{fyi, newsletter, trash}`.

**Why:** Each check catches a different kind of bug.

- *Dry-run* catches me shipping a UI that makes it too easy to click "apply" without realizing.
- *Confidence floor* catches the classifier being 51% sure something is trash when it's actually a needs_reply.
- *Safe-category whitelist* catches a malformed policy (maybe from a future API client, maybe from a future me) that tries to archive `urgent`.

Defense-in-depth on destructive actions is worth the extra lines of code.

**What the whitelist doesn't do:** It doesn't stop a *single-thread* action from touching an urgent email. That's deliberate — the one-at-a-time flow always has a human in the loop; it's bulk that needs the guardrail.

## 5. FastAPI + React, not Streamlit

**Decision:** The UI is a Vite + React SPA talking to FastAPI over `/api/*`. Streamlit is gone.

**Why:** Three concrete capabilities Streamlit doesn't do cleanly:

1. *Optimistic updates.* React Query can fire an archive mutation and update the inbox list locally before the server confirms. Streamlit's re-render-everything model can't match that interactivity.
2. *Rich-text editing.* Quill is a React component. Making Streamlit host a first-class WYSIWYG editor means dropping into custom components that don't look like the rest of the app.
3. *Modal compose.* The compose experience wants to overlay any view and preserve that view's state. Streamlit's stateless-page model fights this.

The baseline's "Streamlit is fine, the AI work is the point" argument was right at the baseline's scope. At this scope — a genuinely interactive inbox — the UI *is* part of the product story.

**Trade-off:** Two toolchains now (Python + Node). For a single-developer portfolio project that's real overhead, but it's the kind of thing future employers expect you to handle anyway.

## 6. The `Draft` model carries `gmail_draft_id`

**Decision:** The `Draft` Pydantic model has an optional `gmail_draft_id`. The push-draft endpoint *updates* the existing Gmail draft if the id is set, and creates a new one otherwise.

**Why:** Without this, every Save-to-Drafts click creates a *new* Gmail draft. Opening Gmail after editing a reply five times would show five ghost drafts for the same thread — horrible UX. Keeping the id on the client-visible model means the UI can track "this edit continues that artifact" naturally.

## 7. Multipart/alternative MIME for rich-text drafts

**Decision:** When the UI sends HTML from Quill alongside plain text, the Gmail client wraps the two in a `multipart/alternative` MIME message.

**Why:** That's the spec — clients that don't render HTML fall back to the plain-text part. Plenty of Gmail recipients open mail in text-only mode (other clients, accessibility readers, someone with images off). Sending HTML-only means they see a stripped-down blob with no line breaks.

**Implementation note:** The plain-text part isn't just a `strip_tags` of the HTML — Quill's HTML has quirks around lists and links that a naive strip mangles. The client runs the HTML through `BeautifulSoup` and generates a reasonable text rendering.

## 8. No auto-send, still

**Decision:** Even in the bulk action layer, `send` is not a policy verb. You can archive a thread, label it, mark it read — but there is no `ActionPolicy.send_categories`.

**Why:** The baseline's "never auto-send" contract was a trust argument. Bulk-sending is that argument cranked to 11. If someone wants to batch-send 20 drafted replies, they can review them in the UI and click Send on each. That's fine. Baking it into a policy would let a future bug or misconfigured UI turn one mistake into twenty.

## 9. The web UI doesn't fetch from Gmail directly

**Decision:** The UI only talks to FastAPI. It never embeds the Google API client in the browser.

**Why:** Two reasons.

1. *Token handling.* The OAuth refresh-token lives in `token.json` on disk. Moving that to the browser means implementing proper key storage, which is a security-sensitive rat hole for a single-user portfolio tool.
2. *Caching.* The classifier and drafter caches are server-side SQLite. If the browser fetched threads directly, the cache would be bypassed for everything except the classify/draft steps.

**Trade-off:** One more hop on every request. Measured latency is dominated by Gmail itself, not the FastAPI layer, so it's not a concern.

## 10. Prompt files are unchanged

**Decision:** `classify.md`, `draft.md`, `brief.md`, `voice.md` are byte-for-byte copies from baseline Sift.

**Why:** The baseline's eval suite is what validated those prompts. Changing a prompt for the robust version would invalidate those measurements and put me in the position of either re-running evals or silently shipping untested prompts. Neither is acceptable, and the prompts don't *need* to change — the robust version's new capabilities are all in the action layer, not the inference layer.

---

## Things I explicitly chose not to build (and why) — robust edition

- **Scheduled "morning run."** A cron/timer that auto-applies the policy at 6 AM every day. Tempting, and obvious next step, but adding a background scheduler means now the app is a daemon, with process supervision, restart semantics, log rotation. For a portfolio tool that the user runs on demand, on-demand is fine. The CLI's `sift apply` + a system cron handles the "I want this automatic" case for anyone who wants to.
- **Server-side rate limiting on the action endpoints.** If someone points a loop at `/api/threads/{id}/archive`, they'll hit Gmail's quota before they hit any limit here. The right layer for this is Gmail itself.
- **Multi-user.** Same as baseline. Still one OAuth, still one user.
- **Undo.** Archive is reversible (move back to inbox), but send-a-reply isn't, and most label changes are reversible via Gmail itself. Building an app-side undo would mean mirroring Gmail's state machine, which is more surface area than the feature earns.
