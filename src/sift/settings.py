"""In-app settings persistence.

Thin helpers that read and upsert values in the repo-root ``.env`` file
without clobbering comments, blank lines, or unrelated keys. Used by the
settings API so the user can paste API keys and pick a provider/model
from the web UI and have those choices survive a restart.

Security notes:

* The UI masks values before echoing them back (see :func:`mask_secret`).
  The server never returns a raw secret in a response body; it returns
  a ``key_set`` boolean plus the masked tail.
* Writes go through an atomic replace (write to ``.env.tmp`` then rename)
  so a crashed write can't leave ``.env`` half-overwritten with secrets
  missing.
* ``.env`` is gitignored in this repo.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from .config import PROJECT_ROOT

ENV_FILE: Path = PROJECT_ROOT / ".env"

# Keys the settings endpoint is allowed to write. Anything not on this list
# is rejected at the edge so a compromised UI can't scribble arbitrary env
# vars into the process (PATH, PYTHONPATH, etc.).
ALLOWED_KEYS: set[str] = {
    "LLM_PROVIDER",
    "SIFT_MODEL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
}

# Which env vars should be masked when echoed back. Everything ending in
# _API_KEY is masked; provider/model are plain strings.
SECRET_KEYS: set[str] = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
}


_LINE_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$")


def mask_secret(value: str) -> str:
    """Render an API key as ``sk-ant-…abcd`` for display.

    Keeps the provider prefix (first 7 chars, up through ``sk-ant-``/``sk-``)
    and the last 4 so Kyle can eyeball whether the right key is loaded
    without ever seeing the middle.
    """
    if not value:
        return ""
    if len(value) <= 12:
        return "…"
    return f"{value[:7]}…{value[-4:]}"


def read_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse the on-disk ``.env`` into a simple key->value dict.

    Comments and blank lines are dropped; quoted values are unquoted.
    Returns an empty dict if the file is missing.
    """
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        # Strip a matching pair of surrounding quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def upsert_env_values(
    updates: dict[str, str],
    *,
    path: Path = ENV_FILE,
) -> None:
    """Write ``updates`` into ``.env``, preserving everything else.

    An existing key's line is rewritten in place. New keys are appended to
    the end of the file. Blank values cause the key to be removed.
    Unrelated keys, comments, and blank lines are left untouched.

    Raises:
        ValueError: if any key is not in :data:`ALLOWED_KEYS`.
    """
    bad = [k for k in updates if k not in ALLOWED_KEYS]
    if bad:
        raise ValueError(f"Refusing to write disallowed env keys: {bad}")

    # Read existing file (or start fresh) as a list of lines so we can edit
    # in place and preserve comments.
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)  # keys we still need to write somewhere

    new_lines: list[str] = []
    for raw in lines:
        m = _LINE_RE.match(raw)
        if not m:
            new_lines.append(raw)
            continue
        key = m.group(1)
        if key in remaining:
            value = remaining.pop(key)
            if value == "":
                # Delete the line (empty value = unset).
                continue
            new_lines.append(f"{key}={_quote_if_needed(value)}")
        else:
            new_lines.append(raw)

    # Any keys not already present get appended.
    if remaining:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        for key, value in remaining.items():
            if value == "":
                continue
            new_lines.append(f"{key}={_quote_if_needed(value)}")

    # Atomic write: temp file + rename so a crash can't corrupt .env.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".env.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines))
            if new_lines and not new_lines[-1].endswith("\n"):
                f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _quote_if_needed(value: str) -> str:
    """Quote values containing whitespace or shell-special chars.

    Not a full shell-escape — we trust the caller (the API layer) to
    validate inputs — but keeps normal provider/model/key strings clean
    while still handling the occasional value with a space in it.
    """
    if any(ch.isspace() for ch in value) or any(ch in value for ch in "#'\"`\\$"):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value
