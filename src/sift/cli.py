"""Command-line interface for SiftRobust.

New in this version (vs. the baseline Sift CLI):
  * ``sift serve``   — launch the FastAPI + Vite web UI (or just the API).
  * ``sift apply``   — bulk-apply an ActionPolicy to recent Gmail threads.
  * ``sift compose`` — fire off a new outbound email without the UI.

Everything from the original CLI (``brief``, ``classify``, ``draft``, ``auth``,
``push-drafts``, ``learn-voice``, ``cache-stats``, ``cache-clear``) still works
unchanged, so an existing Sift user can upgrade in place.

Stays thin on purpose — real logic lives in the library modules so it's easy
to eval and unit-test.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from . import cache
from .brief import build_brief, render_brief, render_brief_llm
from .classifier import classify_threads
from .drafter import draft_replies, draft_reply
from .fixtures import load_labeled_threads
from .models import ActionPolicy, Category, ComposeRequest, Thread

# Windows terminal UTF-8 shim (same reasoning as the baseline CLI: brief
# markdown contains emoji that cp1252 can't encode).
for _stream in (sys.stdout, sys.stderr):
    try:
        if _stream.encoding and _stream.encoding.lower() != "utf-8":
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        pass

app = typer.Typer(
    help="Sift — AI inbox triage, drafts, bulk actions, and a morning brief.",
    no_args_is_help=True,
)
console = Console(legacy_windows=False) if sys.platform == "win32" else Console()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)


class Source(str, Enum):
    fixtures = "fixtures"
    gmail = "gmail"


def _load_threads(
    source: Source,
    *,
    limit: int = 25,
    query: str | None = None,
) -> list[Thread]:
    if source == Source.fixtures:
        return list(load_labeled_threads())
    from . import gmail_client

    return gmail_client.fetch_recent_threads(limit=limit, query=query)


def _gmail_whoami_safe() -> str | None:
    try:
        from . import gmail_client

        return gmail_client.whoami()
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).debug("whoami lookup failed; continuing without user_email")
        return None


# ---------------------------------------------------------------------------
# Core pipeline commands (carried over from baseline Sift)
# ---------------------------------------------------------------------------
@app.command()
def brief(
    source: Annotated[Source, typer.Option(help="Where to pull threads from.")] = Source.fixtures,
    draft: Annotated[bool, typer.Option(help="Also draft replies for urgent/needs_reply threads.")] = True,
    llm_brief: Annotated[bool, typer.Option(help="Use the LLM for the final brief rendering.")] = False,
    limit: Annotated[int, typer.Option(help="Max threads to fetch (Gmail source only).")] = 25,
    query: Annotated[str | None, typer.Option(help="Gmail search query.")] = None,
    push: Annotated[bool, typer.Option(help="Push drafts to Gmail Drafts (Gmail source only).")] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Force fresh classifications/drafts.")] = False,
) -> None:
    """Classify the inbox and print a morning brief."""
    threads = _load_threads(source, limit=limit, query=query)
    console.print(f"[cyan]Classifying {len(threads)} threads...[/cyan]")
    classifications = classify_threads(threads, use_cache=not no_cache)

    drafts = {}
    if draft:
        console.print("[cyan]Drafting replies for urgent + needs_reply...[/cyan]")
        user_email = _gmail_whoami_safe() if source == Source.gmail else None
        drafts = draft_replies(
            threads, classifications, use_cache=not no_cache, user_email=user_email
        )

    brief_data = build_brief(threads, classifications, drafts)
    md = render_brief_llm(brief_data) if llm_brief else render_brief(brief_data)

    console.print()
    console.print(Markdown(md))
    console.print()

    if drafts:
        console.rule("[bold]Drafted replies[/bold]")
        for item in brief_data.items:
            if item.draft is None:
                continue
            console.print(f"\n[bold]{item.thread.from_name}[/bold] — {item.thread.subject}")
            console.print(f"[dim]{item.draft.tone_notes}[/dim]")
            console.print(item.draft.body)

    if push and drafts:
        if source != Source.gmail:
            console.print("\n[yellow]--push ignored: only valid with --source gmail.[/yellow]")
            return
        from . import gmail_client

        console.print(f"\n[cyan]Pushing {len(drafts)} drafts to Gmail...[/cyan]")
        for d in drafts.values():
            gmail_client.push_draft(d)
        console.print(f"[green]Pushed {len(drafts)} draft(s).[/green]")


@app.command()
def classify(
    source: Annotated[Source, typer.Option(help="Where to pull threads from.")] = Source.fixtures,
    limit: Annotated[int, typer.Option(help="Max threads to fetch (Gmail source only).")] = 25,
    query: Annotated[str | None, typer.Option(help="Gmail search query.")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Force fresh classifications.")] = False,
) -> None:
    """Run the classifier only and print per-thread results as a table."""
    threads = _load_threads(source, limit=limit, query=query)
    classifications = classify_threads(threads, use_cache=not no_cache)

    table = Table(title=f"Classified {len(threads)} threads")
    table.add_column("ID", style="dim", width=6)
    table.add_column("From", style="cyan")
    table.add_column("Subject", style="white")
    table.add_column("Category", style="yellow")
    table.add_column("Conf", justify="right")
    class_by_id = {c.thread_id: c for c in classifications}
    for t in threads:
        c = class_by_id[t.id]
        table.add_row(
            t.id,
            t.from_name[:20],
            t.subject[:45],
            c.category.value,
            f"{c.confidence:.2f}",
        )
    console.print(table)


@app.command("draft")
def draft_cmd(
    thread_id: str,
    source: Annotated[Source, typer.Option(help="Where to pull threads from.")] = Source.fixtures,
    push: Annotated[bool, typer.Option(help="Also push the draft to Gmail Drafts (Gmail only).")] = False,
) -> None:
    """Draft a reply for a single thread by ID."""
    threads = _load_threads(source)
    match = next((t for t in threads if t.id == thread_id), None)
    if match is None:
        raise typer.BadParameter(f"No thread with id {thread_id!r}")

    console.print(f"[cyan]Drafting reply for {thread_id}...[/cyan]")
    d = draft_reply(match)
    console.rule(f"[bold]Re: {match.subject}[/bold]")
    console.print(f"[dim]{d.tone_notes}[/dim]\n")
    console.print(d.body)

    if push:
        if source != Source.gmail:
            console.print("\n[yellow]--push ignored: only valid with --source gmail.[/yellow]")
            return
        from . import gmail_client

        draft_id = gmail_client.push_draft(d)
        console.print(f"\n[green]Pushed to Gmail Drafts (id={draft_id}).[/green]")


@app.command("auth")
def auth_cmd(
    force: Annotated[bool, typer.Option(help="Force re-running the browser flow.")] = False,
) -> None:
    """Run (or re-run) the Gmail OAuth flow and cache the token."""
    from . import gmail_client

    try:
        creds = gmail_client.get_credentials(force_refresh=force)
        svc = gmail_client.get_service(creds=creds)
        email = gmail_client.whoami(svc)
    except gmail_client.GmailAuthError as e:
        console.print(f"[red]Gmail auth failed:[/red] {e}")
        raise typer.Exit(code=1) from e

    console.print(f"[green]Authenticated as {email}[/green]")
    console.print(f"Token cached at [dim]{gmail_client.token_file()}[/dim]")
    console.print(
        "[dim]Scope: gmail.modify (read + send + label + draft, in one token).[/dim]"
    )


@app.command("push-drafts")
def push_drafts_cmd(
    limit: Annotated[int, typer.Option(help="Max threads to consider.")] = 25,
    query: Annotated[str | None, typer.Option(help="Gmail search query.")] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Force fresh classifications/drafts.")] = False,
) -> None:
    """Fetch recent Gmail threads, classify + draft, and push drafts to Gmail."""
    from . import gmail_client

    threads = _load_threads(Source.gmail, limit=limit, query=query)
    if not threads:
        console.print("[yellow]No threads found.[/yellow]")
        return

    console.print(f"[cyan]Classifying {len(threads)} threads...[/cyan]")
    classifications = classify_threads(threads, use_cache=not no_cache)

    console.print("[cyan]Drafting replies for urgent + needs_reply...[/cyan]")
    user_email = _gmail_whoami_safe()
    drafts = draft_replies(
        threads, classifications, use_cache=not no_cache, user_email=user_email
    )
    if not drafts:
        console.print("[yellow]Nothing warranted a draft.[/yellow]")
        return

    console.print(f"[cyan]Pushing {len(drafts)} drafts to Gmail...[/cyan]")
    pushed = 0
    for d in drafts.values():
        try:
            gmail_client.push_draft(d)
            pushed += 1
        except gmail_client.GmailActionError as e:
            console.print(f"[yellow]Skipped {d.thread_id}: {e}[/yellow]")
    console.print(f"[green]Created {pushed} Gmail draft(s).[/green]")


@app.command("learn-voice")
def learn_voice_cmd(
    limit: Annotated[int, typer.Option(help="Max sent messages to analyze.")] = 50,
    force: Annotated[bool, typer.Option(help="Re-learn even if a fresh profile exists.")] = False,
) -> None:
    """Learn the user's writing voice from recent sent mail."""
    from . import gmail_client, voice

    user_email = gmail_client.whoami()
    if not force:
        existing = cache.get_cached_voice_profile(
            user_email, max_age_seconds=voice.VOICE_CACHE_TTL_SECONDS
        )
        if existing is not None:
            console.print(
                f"[yellow]Fresh voice profile already cached for {user_email} "
                f"(learned {existing.learned_at}). Pass --force to re-learn.[/yellow]"
            )
            return

    console.print(f"[cyan]Fetching up to {limit} sent messages for {user_email}...[/cyan]")
    messages = gmail_client.fetch_sent_messages(limit=limit)
    if not messages:
        console.print("[yellow]No sent messages found; nothing to learn from.[/yellow]")
        raise typer.Exit(code=1)

    console.print(f"[cyan]Analyzing {len(messages)} messages...[/cyan]")
    profile = voice.learn_voice_profile(messages, user_email=user_email)
    cache.cache_voice_profile(profile)

    console.rule("[bold]Learned voice profile[/bold]")
    console.print(profile.summary)
    if profile.style_examples:
        console.print(f"\n[dim]Captured {len(profile.style_examples)} verbatim style example(s).[/dim]")
    console.print(f"\n[green]Cached for {user_email}.[/green]")


# ---------------------------------------------------------------------------
# NEW in SiftRobust: serve, apply, compose
# ---------------------------------------------------------------------------
@app.command("serve")
def serve_cmd(
    host: Annotated[str, typer.Option(help="API bind host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="API bind port.")] = 8000,
    reload: Annotated[bool, typer.Option(help="Reload on code changes (dev only).")] = True,
    web: Annotated[bool, typer.Option(help="Also start the Vite dev server (`npm run dev`).")] = False,
    web_dir: Annotated[Path, typer.Option(help="Path to the `web/` directory.")] = Path("web"),
) -> None:
    """Launch the FastAPI backend (and optionally the web UI dev server).

    Typical portfolio-demo workflow:

        $ sift serve --web      # starts API on :8000 and Vite on :5173
        $ open http://localhost:5173

    Or run the two separately in different terminals for easier log viewing:

        $ sift serve            # terminal 1: api only
        $ cd web && npm run dev # terminal 2: vite
    """
    try:
        import uvicorn  # noqa: F401
    except ImportError as e:  # pragma: no cover — depends on install
        raise typer.Exit(
            "uvicorn is not installed. Install dev extras: `pip install 'sift[dev]'`."
        ) from e

    web_proc: subprocess.Popen | None = None
    if web:
        if not (web_dir / "package.json").exists():
            raise typer.BadParameter(
                f"No package.json at {web_dir.resolve()} — is the web/ directory set up?"
            )
        console.print(f"[cyan]Starting Vite dev server in {web_dir}...[/cyan]")
        web_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(web_dir),
            shell=sys.platform == "win32",
        )

    try:
        console.print(f"[green]API listening on http://{host}:{port}[/green]")
        console.print("[dim]Swagger docs at /docs · OpenAPI at /openapi.json[/dim]")
        # Inline import keeps typer startup fast for commands that don't need FastAPI.
        import uvicorn

        uvicorn.run(
            "sift.api:app",
            host=host,
            port=port,
            reload=reload,
            reload_dirs=["src"] if reload else None,
        )
    finally:
        if web_proc is not None:
            console.print("[cyan]Stopping Vite dev server...[/cyan]")
            web_proc.terminate()


@app.command("apply")
def apply_cmd(
    limit: Annotated[int, typer.Option(help="Max inbox threads to consider.")] = 50,
    query: Annotated[str | None, typer.Option(help="Gmail search query.")] = None,
    dry_run: Annotated[bool, typer.Option(help="Preview actions without touching Gmail.")] = True,
    min_confidence: Annotated[float, typer.Option(help="Min classifier confidence to act.")] = 0.7,
    archive: Annotated[str, typer.Option(help="Comma-separated categories to archive (fyi, newsletter, trash).")] = "newsletter,trash",
    mark_read: Annotated[str, typer.Option(help="Comma-separated categories to mark read.")] = "newsletter,fyi",
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Force fresh classifications.")] = False,
) -> None:
    """Run the AI-driven bulk-action pipeline end-to-end.

    Safety gates:
      * ``--dry-run`` is on by default. You must flip it off to touch Gmail.
      * Only ``fyi``, ``newsletter``, and ``trash`` are eligible for bulk auto-action.
      * Anything below ``--min-confidence`` is skipped regardless of category.
    """
    from . import actions, gmail_client

    archive_cats = _parse_categories(archive, flag="--archive")
    mark_cats = _parse_categories(mark_read, flag="--mark-read")

    threads = gmail_client.list_inbox(limit=limit, query=query)
    if not threads:
        console.print("[yellow]No threads to process.[/yellow]")
        return

    console.print(f"[cyan]Classifying {len(threads)} threads...[/cyan]")
    classifications = classify_threads(threads, use_cache=not no_cache)

    policy = ActionPolicy(
        dry_run=dry_run,
        min_confidence=min_confidence,
        apply_labels={},
        archive_categories=archive_cats,
        mark_read_categories=mark_cats,
    )

    try:
        report = actions.apply_classifications(threads, classifications, policy)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e

    _print_apply_report(report)


@app.command("compose")
def compose_cmd(
    to: Annotated[str, typer.Option(help="Recipient email address.")],
    subject: Annotated[str, typer.Option(help="Subject line.")],
    body: Annotated[str, typer.Option(help="Plain-text body. Use '-' to read from stdin.")] = "-",
    cc: Annotated[str | None, typer.Option(help="Optional CC.")] = None,
    bcc: Annotated[str | None, typer.Option(help="Optional BCC.")] = None,
    send: Annotated[bool, typer.Option(help="Send immediately. If omitted, saves as draft.")] = False,
) -> None:
    """Send or draft a new outbound email.

    Reads the body from stdin when ``--body -`` (default) so you can pipe:

        $ cat letter.txt | sift compose --to foo@example.com --subject "hi" --send
    """
    from . import gmail_client

    if body == "-":
        body = sys.stdin.read()

    req = ComposeRequest(
        to=to,
        subject=subject,
        body=body,
        body_html=None,
        cc=cc,
        bcc=bcc,
        save_as_draft=not send,
    )
    result = gmail_client.compose(req)
    verb = "Sent" if result["mode"] == "sent" else "Saved draft"
    console.print(f"[green]{verb}.[/green] [dim]id={result['id']}[/dim]")


# ---------------------------------------------------------------------------
# Cache admin
# ---------------------------------------------------------------------------
@app.command("cache-stats")
def cache_stats_cmd() -> None:
    """Show row counts for each cache table."""
    counts = cache.stats()
    db_path = cache.init_db()
    table = Table(title=f"Cache ({db_path})")
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    for name, n in counts.items():
        table.add_row(name, str(n))
    console.print(table)


@app.command("cache-clear")
def cache_clear_cmd(
    table: Annotated[
        str | None,
        typer.Argument(help="Which table to clear. Omit to clear all."),
    ] = None,
) -> None:
    """Wipe cache entries."""
    try:
        n = cache.clear(table)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    label = table or "all tables"
    console.print(f"[green]Cleared {n} rows from {label}.[/green]")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_categories(raw: str, *, flag: str) -> list[Category]:
    """Parse a comma-separated list of category names into Category enum values."""
    out: list[Category] = []
    if not raw.strip():
        return out
    for part in raw.split(","):
        name = part.strip()
        if not name:
            continue
        try:
            out.append(Category(name))
        except ValueError as e:
            valid = ", ".join(c.value for c in Category)
            raise typer.BadParameter(
                f"{flag}: unknown category {name!r}. Valid: {valid}"
            ) from e
    return out


def _print_apply_report(report) -> None:
    verb = "Preview" if report.dry_run else "Applied"
    console.rule(f"[bold]{verb}[/bold]")
    console.print(
        f"{report.total_threads} threads · skipped {report.skipped_low_confidence} "
        f"low-confidence · {len(report.results)} actions"
    )
    table = Table(show_header=True, header_style="bold")
    table.add_column("Thread", style="dim", width=10)
    table.add_column("Action")
    table.add_column("Applied")
    table.add_column("Note", overflow="fold")
    for r in report.results:
        table.add_row(
            r.thread_id[:10],
            r.action,
            "yes" if r.applied else "no",
            r.note,
        )
    console.print(table)
    # Also emit JSON on stdout for piping — handy when scripting.
    if not sys.stdout.isatty():  # pragma: no cover
        print(json.dumps(report.model_dump(), default=str))


if __name__ == "__main__":
    app()
