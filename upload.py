"""
Upload curriculum output to Supabase (raw and lesson pipelines).

PURE DATA UTILITY — no AI calls. Reads the checkpoint files of one
curriculum output directory and writes domain → curriculum_levels → books →
topics → content_chunks (+ topic_learning_objectives, topic_prerequisites,
exercises). For the lesson pipeline (``manifest.pipeline == "lesson"``, or a
directory holding both ``questions/`` and ``cards/``) it also writes the
practice content authored by questions.py / cards.py into
``reading_questions``, ``topic_questions``, ``topic_cards`` and
``topic_exercises`` (see docs/artifacts.md, "Upload mapping"). Images listed
on a chunk are uploaded to the ``curriculum-images`` Storage bucket and every
``![alt](image:<id>)`` placeholder is rewritten to the public URL before the
chunk is inserted; chunks with images but no placeholder keep the legacy
behaviour (markdown appended at the end of the content).

Usage — flag form (unchanged for existing callers; ``--target`` is required):

  python upload.py --input output/<name>/ --target default --owner user --user-id <uuid>
  python upload.py --input output/<name>/ --target staging --owner user --user-email me@example.com
  python upload.py --input output/<name>/ --target custom --db-url <url> --db-key <key> \\
                   --owner org --org-id <uuid>
  python upload.py --input output/<name>/ --target staging --owner user --user-id <uuid> \\
                   --update [--replace-chunks]
  python upload.py --input output/<name>/ --target staging --enrich
  python upload.py --input output/<name>/ --target staging --owner user --user-id <uuid> --dry-run

Usage — subcommand form (stage-script convention):

  python upload.py check  output/<name>/ [--item g:<slug>]   # resolvability gates, no DB
  python upload.py plan   output/<name>/ --target staging ...  # same as --dry-run
  python upload.py upload output/<name>/ --target staging ...
  python upload.py enrich output/<name>/ --target staging

Targets: ``default`` reads SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY, ``staging``
reads STAGING_SUPABASE_URL / STAGING_SUPABASE_SERVICE_ROLE_KEY, ``custom`` takes
``--db-url``/``--db-key`` (or CUSTOM_DB_URL / CUSTOM_DB_SERVICE_KEY). ``.env``
discovery walks up from this file's directory (curriculum_creator/.env, then
dutato/.env). A non-default target whose URL equals SUPABASE_URL is refused so
a misconfigured staging can never write to prod. Keys are never printed.

Module API (used by validate.py / critique.py):
  check(input_dir, item_id=None) -> list[Finding]      upload-side gates for the whole output
  check_item(input_dir, item_id) -> list[Finding]      the same gates for one lesson id
  main(argv) -> int                                    0 success, 1 errors, 2 config/usage error

Writes ``output/<name>/upload_result.json`` (keys listed in docs/artifacts.md)
at the end of every successful upload or enrich; ``--dry-run`` prints the plan
and writes nothing.

Interrupted runs: a request that fails for a passing reason (connection reset,
TLS alert, timeout, unresolved host name, a gateway or server reply of HTTP
408/425/429/500/502-504 or Cloudflare 520-524, PostgREST PGRST000-003,
serialization failure, deadlock, statement timeout, database shutting down or
out of connections) is tried RETRY_ATTEMPTS times with a doubling wait. A
database error that PostgREST reports in its own JSON body is judged by its
code, not by the HTTP status. A retried insert first reads back which rows its
failed try wrote, so nothing is written twice. Images are uploaded before any
database write: an image that keeps failing that way, or a page placeholder
whose image could not be uploaded, stops the run with the database untouched
(any other failed image is a warning and its page goes up without it). When a
request still fails the run exits 1 without a traceback; the error says
whether the database was touched and which command finishes the upload (a
first upload: the same command with ``--update --replace-chunks``; an
``--update`` run or ``enrich``: the same command again). An upload that stops
after it began writing also removes an earlier ``upload_result.json`` of the
same target, so status.py does not report a partial upload as done (``enrich``
keeps the file: it reads the domain id from it). A plain upload onto a domain
that already holds topics of this curriculum is refused (``plan`` reports it
too), since it would insert them a second time.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlparse

import _compat  # noqa: F401
import httpx
from dotenv import load_dotenv
from postgrest.exceptions import APIError
from rich.console import Console
from rich.markup import escape

from contracts import (
    BLOOM_LEVELS,
    BLOOM_TO_MASTERY,
    CARD_TYPES,
    CARDS_DIR,
    CONTRACT_VERSION,
    EMBEDDED_DEFAULT_POSITION,
    EMBEDDED_KINDS,
    GROUP_ID_RE,
    IMAGE_PLACEHOLDER_RE,
    LESSONS_DIR,
    PIPELINE_LESSON,
    QUESTION_POSITIONS,
    QUESTIONS_DIR,
    QUIZ_TYPES,
    Finding,
    dump_json,
    filename_to_item_id,
    list_item_files,
    load_json,
    pipeline_mode,
)

console = Console()

TARGETS = ("default", "staging", "custom")
PRACTICE_TABLES = ("reading_questions", "topic_questions", "topic_cards", "topic_exercises")
# PostgREST error codes meaning "no such table": PGRST205 (schema cache miss) and
# 42P01 (undefined_table, surfaced when the cache is stale).
MISSING_TABLE_CODES = {"PGRST205", "42P01"}
TOPIC_QUESTION_PAYLOAD_KEYS = (
    "options", "correct_index", "acceptance", "key_points", "expected_answer", "rationale",
    "feedback", "distractors", "variants", "lo_id", "concept_id", "source_quote", "recap_of",
)
EXERCISE_CHUNK_OFFSET = 1000  # exercise-as-chunk rows live at chunk_index >= this
BATCH_SIZE = 50
# Reads of "every row of ..." are paged: PostgREST silently caps one response at its
# max-rows setting (1000 on Supabase), and update mode would re-insert what it did not see.
SELECT_PAGE_SIZE = 1000
LIST_USERS_PAGE_SIZE = 1000

# Retries. A request is tried RETRY_ATTEMPTS times in all; the wait before a retry
# starts at RETRY_BASE_DELAY seconds and doubles, capped at RETRY_MAX_DELAY.
RETRY_ATTEMPTS = 5
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 30.0
# HTTP statuses of a gateway or server that is briefly unavailable (520-524: Cloudflare).
TRANSIENT_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})
# PostgREST could not reach the database (PGRST000-003), and SQLSTATEs that pass on
# their own: serialization failure, deadlock, statement timeout, server shutting down.
TRANSIENT_DB_CODES = frozenset({
    "PGRST000", "PGRST001", "PGRST002", "PGRST003",
    "40001", "40P01", "57014", "57P01", "57P02", "57P03",
})
TRANSIENT_SQLSTATE_CLASSES = ("08", "53")  # connection exception, insufficient resources
CURRICULUM_IMAGES_BUCKET = "curriculum-images"
UPLOAD_RESULT_FILE = "upload_result.json"
SUBCOMMANDS = ("check", "plan", "upload", "enrich")

LEVEL_NAMES = {1: "Fundamentals", 2: "Intermediate", 3: "Advanced"}
LEVEL_DESCRIPTIONS = {
    1: "Definitions, core concepts, basic examples",
    2: "Techniques, patterns, comparative analysis",
    3: "Complex applications, trade-offs, synthesis",
}


class UploadError(Exception):
    """Fatal problem reported to the user; ``exit_code`` is what main() returns."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class TransientUploadError(UploadError):
    """A request kept failing for a passing reason (network, gateway, busy server)
    after every retry. The run stops; what it already wrote stays."""


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------

T = TypeVar("T")
_sleep = time.sleep  # module-level so tests can replace the wait


def _error_chain(exc: BaseException) -> Iterator[BaseException]:
    """``exc`` followed by the errors it was raised from (``__cause__`` / ``__context__``)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _http_statuses(exc: BaseException) -> Iterator[int]:
    """HTTP statuses carried by one error, whatever client library raised it."""
    if isinstance(exc, httpx.HTTPStatusError):
        yield exc.response.status_code
    for name in ("status", "status_code", "code"):
        value = getattr(exc, name, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            yield value
        elif isinstance(value, str) and len(value) == 3 and value.isdigit():
            yield int(value)


def _is_transient(exc: BaseException) -> bool:
    """True when repeating the same request may succeed.

    Covers transport failures (connection reset, TLS alert, timeout, a host name
    that does not resolve), gateway and server statuses (TRANSIENT_HTTP_STATUS) and
    the database codes in
    TRANSIENT_DB_CODES / TRANSIENT_SQLSTATE_CLASSES. The whole cause chain is
    read because the storage client hides the real HTTP status of a reply it
    cannot parse (it reports 400, or lets a JSONDecodeError through). Constraint
    violations, permission errors and missing tables are never transient.
    """
    if isinstance(exc, UploadError):  # ours: already final (a TransientUploadError has used its retries)
        return False
    chain = list(_error_chain(exc))
    # A certificate the client refuses stays refused. (A name that does not resolve is
    # retried: that is also what a dropped connection looks like.)
    if any(isinstance(err, ssl.SSLCertVerificationError) for err in chain):
        return False
    for err in chain:
        if isinstance(err, httpx.TransportError) and not isinstance(err, httpx.UnsupportedProtocol):
            return True
        if isinstance(err, (ConnectionError, TimeoutError, ssl.SSLError)):
            return True
        if type(err).__name__ == "AuthRetryableError":  # supabase_auth: 502/503/504 or no reply
            return True
        if any(status in TRANSIENT_HTTP_STATUS for status in _http_statuses(err)):
            return True
        code = getattr(err, "code", None)
        if isinstance(code, str) and (
            code in TRANSIENT_DB_CODES or (len(code) == 5 and code[:2] in TRANSIENT_SQLSTATE_CLASSES)
        ):
            return True
    return False


def _describe_error(exc: BaseException) -> str:
    """One line naming the error, short enough for the console."""
    text = " ".join(str(exc).split())
    if len(text) > 200:
        text = text[:197] + "..."
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _retry(what: str, call: Callable[[], T]) -> T:
    """Run one request, repeating it with a growing wait while the failure is transient.

    ``call`` must build the request anew on each call and be safe to repeat: a
    read, a delete or update by filter, or an insert made repeatable by
    ``_insert_batch``. Raises TransientUploadError after RETRY_ATTEMPTS failed
    tries; any other failure is raised unchanged on the spot.
    """
    delay = RETRY_BASE_DELAY
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return call()
        except UploadError:
            raise
        except Exception as e:  # noqa: BLE001 — each client library has its own error family
            if not _is_transient(e):
                raise
            if attempt == RETRY_ATTEMPTS:
                raise TransientUploadError(
                    f"{what} failed {RETRY_ATTEMPTS} times in a row ({_describe_error(e)})"
                ) from e
            console.print(
                f"    [yellow]↻[/] {escape(what)}: {escape(_describe_error(e))} — "
                f"retrying in {delay:g}s (try {attempt + 1} of {RETRY_ATTEMPTS})"
            )
            _sleep(delay)
            delay = min(delay * 2, RETRY_MAX_DELAY)
    raise AssertionError("unreachable")  # pragma: no cover


def _execute(what: str, build: Callable[[], Any]) -> Any:
    """Build and execute one repeatable PostgREST request, with retries."""
    return _retry(what, lambda: build().execute())


def _select_all(what: str, build: Callable[[], Any]) -> list[dict]:
    """Every row of a select, read in pages ordered by ``id``.

    ``build`` returns the filtered select. Reading stops at the first empty page,
    so the result is complete whatever row cap the server applies to a response.
    """
    rows: list[dict] = []
    while True:
        start = len(rows)
        page = _execute(
            what, lambda: build().order("id").range(start, start + SELECT_PAGE_SIZE - 1),
        ).data or []
        if not page:
            return rows
        rows.extend(page)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _sanitize(text: str | None) -> str | None:
    """Remove null bytes that PostgreSQL rejects."""
    if text is None:
        return None
    return text.replace("\x00", "").replace("\u0000", "")


def _clamp_bloom(value: str | None, default: str) -> str:
    """Clamp bloom_level to the valid enum, warning on invalid values."""
    if value in BLOOM_LEVELS:
        return value
    if value is not None:
        console.print(f"  [yellow]Warning:[/] invalid bloom_level {value!r} — using {default!r}")
    return default


def _batches(rows: list[dict]) -> Iterator[list[dict]]:
    for i in range(0, len(rows), BATCH_SIZE):
        yield rows[i:i + BATCH_SIZE]


def _insert_batch(client: Any, table: str, rows: list[dict]) -> None:
    """Insert one batch of rows that all carry a client-generated ``id``, with retries.

    A request that failed in transit may still have been committed. Every retry
    therefore first reads back which of the batch's ids exist and sends only the
    others, so a repeated insert neither duplicates rows nor fails on the rows
    its own first try wrote. A unique violation on a retry is accepted when the
    whole batch turns out to be there (the first try committed in between).
    """
    ids = [row["id"] for row in rows]
    retrying = False

    def landed() -> set[str]:
        found = client.table(table).select("id").in_("id", ids).execute().data or []
        return {row["id"] for row in found}

    def attempt() -> None:
        nonlocal retrying
        if not retrying:
            retrying = True
            client.table(table).insert(rows).execute()
            return
        written = landed()
        todo = [row for row in rows if row["id"] not in written]
        if not todo:
            return
        try:
            client.table(table).insert(todo).execute()
        except APIError as e:
            # The failed try was still running when the read-back looked: it has
            # committed since, and this try collided with its rows.
            if str(e.code or "") == "23505" and landed() >= set(ids):
                return
            raise

    _retry(f"insert into {table}", attempt)


def _insert_rows(client: Any, table: str, rows: list[dict]) -> int:
    """Insert rows in batches of BATCH_SIZE; returns the number inserted."""
    inserted = 0
    for sub in _batches(rows):
        _insert_batch(client, table, sub)
        inserted += len(sub)
    return inserted


def _topics_of(structure: Any) -> list[dict]:
    return structure if isinstance(structure, list) else structure.get("topics", [])


def _walk_topics(topics: list[dict], parent_path: list[str] | None = None):
    """Yield (topic_dict, topic_path) for every node in the tree."""
    if parent_path is None:
        parent_path = []
    for item in topics:
        path = parent_path + [item["title"]]
        yield item, path
        yield from _walk_topics(item.get("children", []), path)


def _count_topics(topics: list[dict]) -> int:
    """Count total topics including children."""
    return sum(1 + _count_topics(t.get("children", [])) for t in topics)


def _lookup_title(path_to_id: Mapping[str, str], title: str | None) -> str | None:
    """Exact then case-insensitive title lookup in a title -> topic id map."""
    if not title:
        return None
    if title in path_to_id:
        return path_to_id[title]
    lower = title.lower()
    for key, value in path_to_id.items():
        if key.lower() == lower:
            return value
    return None


def _lesson_titles(topics: list[dict]) -> dict[str, str]:
    """lesson_id -> leaf title, from structure leaves written by ``lessons.py build``."""
    out: dict[str, str] = {}
    for topic, _ in _walk_topics(topics):
        lesson_id = topic.get("lesson_id")
        if lesson_id and not topic.get("children"):
            out[lesson_id] = topic["title"]
    return out


def _is_lesson_mode(input_dir: Path) -> bool:
    """Lesson pipeline: manifest says so, or both practice directories exist."""
    if pipeline_mode(input_dir) == PIPELINE_LESSON:
        return True
    return (input_dir / QUESTIONS_DIR).is_dir() and (input_dir / CARDS_DIR).is_dir()


def _slug_of(manifest: dict, input_dir: Path) -> str:
    return manifest.get("domain", input_dir.name).lower().replace(" ", "-")


# ---------------------------------------------------------------------------
# Targets, client, owner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """A resolved database target. ``key`` is excluded from repr so it is never printed."""

    name: str
    url: str
    key: str = field(repr=False)

    @property
    def host(self) -> str:
        return urlparse(self.url).netloc or self.url


def _norm_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/").lower()


def resolve_target(
    target: str,
    db_url: str | None = None,
    db_key: str | None = None,
    env: Mapping[str, str] | None = None,
) -> Target:
    """Resolve the URL/key for ``target`` from flags and environment.

    ``env`` defaults to ``os.environ`` after ``load_dotenv()`` (discovery walks
    up from this file's directory). Tests pass an explicit mapping to bypass
    ``.env``. Raises UploadError(exit_code=2) on missing variables or when a
    non-default target points at SUPABASE_URL.
    """
    if target not in TARGETS:
        raise UploadError(f"Unknown target {target!r}; expected one of {', '.join(TARGETS)}", 2)
    if env is None:
        load_dotenv()
        env = os.environ

    if target == "custom":
        url = db_url or env.get("CUSTOM_DB_URL")
        key = db_key or env.get("CUSTOM_DB_SERVICE_KEY")
        hint = "pass --db-url and --db-key, or set CUSTOM_DB_URL and CUSTOM_DB_SERVICE_KEY"
    elif target == "staging":
        url = env.get("STAGING_SUPABASE_URL")
        key = env.get("STAGING_SUPABASE_SERVICE_ROLE_KEY")
        hint = "set STAGING_SUPABASE_URL and STAGING_SUPABASE_SERVICE_ROLE_KEY in .env"
    else:
        url = env.get("SUPABASE_URL")
        key = env.get("SUPABASE_SERVICE_ROLE_KEY")
        hint = "set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env"

    if not url or not key:
        raise UploadError(f"Database credentials for target '{target}' are not set: {hint}", 2)

    prod_url = env.get("SUPABASE_URL")
    if target != "default" and prod_url and _norm_url(prod_url) == _norm_url(url):
        host = urlparse(url).netloc or url
        raise UploadError(
            f"Refusing to run: target '{target}' resolves to {host}, which is also "
            f"SUPABASE_URL (the default/prod database). Fix the {target} URL variable.",
            2,
        )
    return Target(name=target, url=url, key=key)


def get_client(target: Target) -> Any:
    """Create a Supabase client for a resolved target."""
    from supabase import create_client

    return create_client(target.url, target.key)


def _user_field(user: Any, name: str) -> Any:
    return user.get(name) if isinstance(user, dict) else getattr(user, name, None)


def _find_user_by_email_rpc(client: Any, email: str) -> str | None:
    """Resolve an email through the ``find_user_by_email`` RPC; None on failure or no match."""
    try:
        result = _execute("resolve owner email", lambda: client.rpc("find_user_by_email", {"p_email": email}))
    except TransientUploadError:
        raise  # the target is unreachable: the admin listing would only fail the same way
    except Exception as e:  # noqa: BLE001 — any other API failure falls back to admin listing
        console.print(
            f"  [yellow]→[/] find_user_by_email RPC unavailable ({type(e).__name__}); "
            "falling back to the admin user listing"
        )
        return None
    data = result.data
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict):
        data = data.get("id") or data.get("find_user_by_email")
    return str(data) if data else None


def _find_user_by_email_admin(client: Any, email: str) -> str | None:
    """Page through ``auth.admin.list_users`` until a short page; case-insensitive match."""
    wanted = email.lower()
    page = 1
    while True:
        users = _retry(
            "list users", lambda: client.auth.admin.list_users(page=page, per_page=LIST_USERS_PAGE_SIZE),
        ) or []
        for user in users:
            if (_user_field(user, "email") or "").lower() == wanted:
                user_id = _user_field(user, "id")
                return str(user_id) if user_id else None
        if len(users) < LIST_USERS_PAGE_SIZE:
            return None
        page += 1


def resolve_owner_id(client: Any, user_id: str | None = None, user_email: str | None = None) -> str | None:
    """Return the owner uuid from ``--user-id`` or by resolving ``--user-email``.

    Email resolution tries the ``find_user_by_email`` RPC first and falls back
    to the paginated admin user listing. Raises UploadError(exit_code=2) when
    both flags are given or the email matches no user.
    """
    if user_id and user_email:
        raise UploadError("--user-id and --user-email are mutually exclusive", 2)
    if user_id:
        return user_id
    if not user_email:
        return None
    email = user_email.strip()
    found = _find_user_by_email_rpc(client, email) or _find_user_by_email_admin(client, email)
    if not found:
        raise UploadError(f"No user found for email {email!r} on this target", 2)
    console.print(f"  [green]✓[/] Resolved owner {email} → {found}")
    return found


def has_table(client: Any, name: str) -> bool:
    """True when ``name`` is reachable through PostgREST; False when the table is missing.

    Any APIError other than a missing-table error is re-raised. A transient failure
    is retried and, if it lasts, stops the run (TransientUploadError): PostgREST's
    "could not query the database for the schema cache" must never be read as
    "the table is missing", which would silently skip the practice content.
    """
    try:
        _execute(f"probe table {name}", lambda: client.table(name).select("id").limit(0))
    except APIError as e:
        code = str(e.code or "")
        message = (e.message or "").lower()
        if code in MISSING_TABLE_CODES or "schema cache" in message or "does not exist" in message:
            return False
        raise
    return True


def probe_practice_tables(client: Any) -> dict[str, bool]:
    """Probe the four practice tables once per run."""
    tables = {name: has_table(client, name) for name in PRACTICE_TABLES}
    missing = [name for name, present in tables.items() if not present]
    if missing:
        console.print(
            f"  [yellow]Warning:[/] missing tables on this target: {', '.join(missing)} — "
            "their rows are skipped (topic_exercises falls back to exercise chunks)"
        )
    return tables


# ---------------------------------------------------------------------------
# Validation pre-flight (validate.py) and upload-side gates (check)
# ---------------------------------------------------------------------------


def _validate_or_exit(input_dir: Path, stage: str, skip: bool) -> None:
    """Run the pre-flight validator; abort on errors (warnings never block)."""
    if skip:
        console.print("[yellow]Pre-upload validation skipped (--skip-validation)[/]")
        return
    from validate import run_validation

    findings = run_validation(input_dir, stage)
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    if warnings:
        console.print(
            f"  [yellow]{len(warnings)} validation warning(s)[/] — see "
            f"`python validate.py {input_dir}` for details"
        )
    if errors:
        console.print(f"[red]Validation failed with {len(errors)} error(s):[/]")
        for f in errors:
            console.print(f"  [red]{f.check_id}[/] {f.location}: {f.message}")
        raise UploadError(
            "Fix the errors above (or re-run with --skip-validation to override at your own risk).",
            1,
        )


@dataclass
class Inputs:
    manifest: dict
    topics: list[dict]
    chunks: list[dict]


def _load_inputs(input_dir: Path, findings: list[Finding]) -> Inputs | None:
    """Load manifest/structure/chunks, appending a finding per missing or invalid file."""
    loaded: dict[str, Any] = {}
    for name in ("manifest.json", "structure.json", "chunks.json"):
        path = input_dir / name
        if not path.exists():
            findings.append(Finding("upload.file_missing", "error", name, f"{name} not found in {input_dir}"))
            continue
        try:
            loaded[name] = load_json(path)
        except json.JSONDecodeError as e:
            findings.append(Finding("upload.invalid_json", "error", name, str(e)))
    if len(loaded) < 3:
        return None
    return Inputs(loaded["manifest.json"], _topics_of(loaded["structure.json"]), loaded["chunks.json"])


@dataclass
class LessonPractice:
    """Practice artifacts of one lesson (any of the three files may be absent)."""

    lesson_id: str
    title: str | None = None
    questions: dict | None = None
    cards: dict | None = None


@dataclass
class PracticeContent:
    lessons: dict[str, LessonPractice] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)


def load_practice_content(input_dir: Path) -> PracticeContent:
    """Read lessons/, questions/ and cards/ item files keyed by lesson id."""
    content = PracticeContent()

    def lesson(lesson_id: str) -> LessonPractice:
        return content.lessons.setdefault(lesson_id, LessonPractice(lesson_id))

    for stage_dir, attr in ((LESSONS_DIR, "title"), (QUESTIONS_DIR, "questions"), (CARDS_DIR, "cards")):
        for path in list_item_files(input_dir / stage_dir):
            if not GROUP_ID_RE.match(filename_to_item_id(path.name)):
                continue  # helper files (lessons/anchor.json) are not lesson items
            rel = f"{stage_dir}/{path.name}"
            try:
                data = load_json(path)
            except json.JSONDecodeError as e:
                content.findings.append(Finding("upload.invalid_json", "error", rel, str(e)))
                continue
            if not isinstance(data, dict):
                content.findings.append(Finding("upload.invalid_shape", "error", rel, "Expected a JSON object"))
                continue
            lesson_id = data.get("lesson_id") or filename_to_item_id(path.name)
            if attr == "title":
                lesson(lesson_id).title = data.get("title")
            else:
                setattr(lesson(lesson_id), attr, data)
    return content


def _chunk_indices_by_title(chunks: list[dict]) -> dict[str, set[int]]:
    """lower-cased leaf title -> set of chunk_index values present in chunks.json."""
    out: dict[str, set[int]] = {}
    for chunk in chunks:
        topic_path = chunk.get("topic_path") or []
        if not topic_path:
            continue
        index = chunk.get("chunk_index", 0)
        if isinstance(index, int):
            out.setdefault(str(topic_path[-1]).lower(), set()).add(index)
    return out


def _check_titles(topics: list[dict], lesson_mode: bool) -> list[Finding]:
    """Topics are keyed by title at upload time, so duplicates would collide."""
    seen: dict[str, list[str]] = {}
    for topic, path in _walk_topics(topics):
        seen.setdefault(topic["title"].lower(), []).append(" > ".join(path))
    severity = "error" if lesson_mode else "warning"
    return [
        Finding(
            "upload.duplicate_title", severity, locs[0],
            f"Title {locs[0].split(' > ')[-1]!r} appears {len(locs)} times ({'; '.join(locs)}) — "
            "the uploader keys topics by title, so chunks and practice content would collide",
        )
        for locs in seen.values()
        if len(locs) > 1
    ]


def _image_file(input_dir: Path, local_path: str) -> Path | None:
    """Resolve an image local_path against extracted/ then the output dir itself."""
    if not local_path:
        return None
    for base in (input_dir / "extracted", input_dir):
        candidate = base / local_path
        if candidate.exists():
            return candidate
    return None


def _check_images(chunks: list[dict], input_dir: Path) -> list[Finding]:
    """Every placeholder resolves to a chunk image whose file exists on disk."""
    findings: list[Finding] = []
    for i, chunk in enumerate(chunks):
        images = chunk.get("images") or []
        loc = f"chunks.json[{i}] ({' > '.join(chunk.get('topic_path') or [])})"
        ids = {img.get("id") for img in images if img.get("id")}
        placeholder_ids = {m.group("id") for m in IMAGE_PLACEHOLDER_RE.finditer(chunk.get("content", ""))}
        unknown = sorted(placeholder_ids - ids)
        if unknown:
            findings.append(Finding(
                "upload.image_placeholder_unresolved", "error", loc,
                f"Placeholder image id(s) {unknown} are not in the chunk's images list",
            ))
        for img in images:
            if img.get("id") and not _image_file(input_dir, img.get("local_path", "")):
                severity = "error" if img["id"] in placeholder_ids else "warning"
                findings.append(Finding(
                    "upload.image_file_missing", severity, loc,
                    f"Image {img['id']!r} file {img.get('local_path')!r} not found — "
                    + ("its placeholder cannot be resolved" if severity == "error" else "it will be skipped"),
                ))
    return findings


def _check_page_index(
    value: Any, chunk_indices: set[int], loc: str, required: bool, findings: list[Finding],
) -> None:
    if value is None:
        if required:
            findings.append(Finding("upload.field_missing", "error", loc, "page_index is required"))
        return
    if not isinstance(value, int) or value not in chunk_indices:
        findings.append(Finding(
            "upload.page_index_unresolved", "error", loc,
            f"page_index {value!r} matches no chunk of this lesson (chunk_index values: {sorted(chunk_indices)})",
        ))


def _check_lesson_practice(
    lesson: LessonPractice,
    lesson_to_title: Mapping[str, str],
    leaf_titles: set[str],
    chunk_indices_by_title: Mapping[str, set[int]],
) -> list[Finding]:
    """Gates for one lesson: topic resolvable, enums valid, page indices resolvable."""
    findings: list[Finding] = []
    title = lesson_to_title.get(lesson.lesson_id) or lesson.title
    if not title or title.lower() not in leaf_titles:
        findings.append(Finding(
            "upload.lesson_unresolved", "error", lesson.lesson_id,
            f"No structure leaf carries lesson_id {lesson.lesson_id!r}"
            + (f" and no leaf is titled {title!r}" if title else " and lessons/ gives no title to fall back on"),
        ))
        return findings
    chunk_indices = chunk_indices_by_title.get(title.lower(), set())

    questions = lesson.questions or {}
    for i, q in enumerate(questions.get("embedded") or []):
        loc = q.get("id") or f"{lesson.lesson_id} embedded[{i}]"
        if q.get("kind") not in EMBEDDED_KINDS:
            findings.append(Finding("upload.enum_invalid", "error", loc, f"kind {q.get('kind')!r} not in {EMBEDDED_KINDS}"))
        if q.get("position") is not None and q.get("position") not in QUESTION_POSITIONS:
            findings.append(Finding("upload.enum_invalid", "error", loc, f"position {q.get('position')!r} not in {QUESTION_POSITIONS}"))
        if not (q.get("prompt") or "").strip():
            findings.append(Finding("upload.field_missing", "error", loc, "prompt is empty"))
        _check_page_index(q.get("page_index"), chunk_indices, loc, True, findings)
    for i, q in enumerate(questions.get("quiz") or []):
        loc = q.get("id") or f"{lesson.lesson_id} quiz[{i}]"
        if q.get("type") not in QUIZ_TYPES:
            findings.append(Finding("upload.enum_invalid", "error", loc, f"type {q.get('type')!r} not in {QUIZ_TYPES}"))
        if not (q.get("prompt") or "").strip():
            findings.append(Finding("upload.field_missing", "error", loc, "prompt is empty"))
        if q.get("bloom_level") not in BLOOM_LEVELS:
            findings.append(Finding(
                "upload.enum_invalid", "warning", loc,
                f"bloom_level {q.get('bloom_level')!r} not in {BLOOM_LEVELS} — upload clamps it to 'understand'",
            ))
        _check_page_index(q.get("page_index"), chunk_indices, loc, False, findings)
    for i, ex in enumerate(questions.get("exercises") or []):
        loc = ex.get("id") or f"{lesson.lesson_id} exercises[{i}]"
        if not (ex.get("problem_statement") or "").strip():
            findings.append(Finding("upload.field_missing", "error", loc, "problem_statement is empty"))

    for i, card in enumerate((lesson.cards or {}).get("cards") or []):
        loc = card.get("id") or f"{lesson.lesson_id} cards[{i}]"
        if card.get("card_type") not in CARD_TYPES:
            findings.append(Finding("upload.enum_invalid", "error", loc, f"card_type {card.get('card_type')!r} not in {CARD_TYPES}"))
        for key in ("front", "back"):
            if not (card.get(key) or "").strip():
                findings.append(Finding("upload.field_missing", "error", loc, f"{key} is empty"))
        _check_page_index(card.get("page_index"), chunk_indices, loc, False, findings)
    return findings


def check(input_dir: Path, item_id: str | None = None, **opts: Any) -> list[Finding]:
    """Upload-side gates that need no database: files load, titles are unique,
    image placeholders resolve, and (lesson mode) every practice file maps to a
    structure leaf with resolvable page indices and valid enums.

    With ``item_id`` only that lesson's practice gates run (plus file loading).
    """
    input_dir = Path(input_dir)
    findings: list[Finding] = []
    inputs = _load_inputs(input_dir, findings)
    if inputs is None:
        return findings
    lesson_mode = _is_lesson_mode(input_dir)

    if item_id is None:
        findings.extend(_check_titles(inputs.topics, lesson_mode))
        findings.extend(_check_images(inputs.chunks, input_dir))
    if not lesson_mode:
        return findings

    practice = load_practice_content(input_dir)
    findings.extend(practice.findings)
    lesson_to_title = _lesson_titles(inputs.topics)
    leaf_titles = {t["title"].lower() for t, _ in _walk_topics(inputs.topics) if not t.get("children")}
    chunk_indices = _chunk_indices_by_title(inputs.chunks)

    if item_id is not None:
        lesson = practice.lessons.get(item_id)
        if lesson is None:
            return findings + [Finding(
                "upload.item_missing", "error", item_id,
                f"No lessons/, questions/ or cards/ file for {item_id!r}",
            )]
        return findings + _check_lesson_practice(lesson, lesson_to_title, leaf_titles, chunk_indices)

    if not practice.lessons:
        findings.append(Finding(
            "upload.no_practice_content", "info", f"{QUESTIONS_DIR}/, {CARDS_DIR}/",
            "Lesson pipeline but no questions/ or cards/ files — only topics and chunks will be uploaded",
        ))
    for lesson in practice.lessons.values():
        findings.extend(_check_lesson_practice(lesson, lesson_to_title, leaf_titles, chunk_indices))
    for lesson_id, title in lesson_to_title.items():
        lesson = practice.lessons.get(lesson_id)
        if lesson is None or lesson.questions is None:
            findings.append(Finding(
                "upload.lesson_without_questions", "warning", f"{title} ({lesson_id})",
                "Structure leaf has no questions/ file — the lesson uploads without practice content",
            ))
    return findings


def check_item(input_dir: Path, item_id: str, **opts: Any) -> list[Finding]:
    """Run the upload gates for a single lesson id."""
    return check(input_dir, item_id=item_id, **opts)


def print_findings(findings: list[Finding]) -> tuple[int, int]:
    """Print findings in the shared stage-script format; returns (errors, warnings)."""
    tags = {"error": "ERROR", "warning": "WARN", "info": "INFO"}
    for f in sorted(findings, key=lambda f: (("error", "warning", "info").index(f.severity), f.check_id)):
        print(f"[{tags[f.severity]}] {f.check_id} @ {f.location}: {f.message}")
    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    print(f"{errors} errors, {warnings} warnings")
    return errors, warnings


def _gate_or_raise(input_dir: Path) -> Inputs:
    """Run check(), abort on errors, and return the loaded manifest/topics/chunks."""
    errors, _ = print_findings(check(input_dir))
    if errors:
        raise UploadError(f"Upload gates failed with {errors} error(s); fix them before writing.", 1)
    inputs = _load_inputs(input_dir, [])
    if inputs is None:  # defensive: check() already reports unloadable files as errors
        raise UploadError(f"{input_dir}: manifest.json, structure.json or chunks.json could not be loaded", 1)
    return inputs


# ---------------------------------------------------------------------------
# Domain, levels, books, topics
# ---------------------------------------------------------------------------


def create_domain(
    client: Any,
    name: str,
    slug: str,
    description: str = "",
    owner_id: str | None = None,
    org_id: str | None = None,
    icon_name: str = "school",
    sort_order: int = 99,
    domain_family: str | None = None,
    variant: str | None = None,
) -> str:
    """Create a new domain or return its ID if one with this slug already exists.

    If the domain already exists but is missing ``domain_family`` / ``variant``,
    back-fill those columns from the arguments so an extensive curriculum
    uploaded before its variants still groups with them (migration 029).
    """
    result = _execute(
        "read domain", lambda: client.table("domains").select("id, domain_family, variant").eq("slug", slug),
    )
    if result.data:
        row = result.data[0]
        domain_id = row["id"]
        update: dict[str, str] = {}
        if domain_family and not row.get("domain_family"):
            update["domain_family"] = _sanitize(domain_family)
        if variant and not row.get("variant"):
            update["variant"] = variant
        if update:
            _execute("update domain", lambda: client.table("domains").update(update).eq("id", domain_id))
            console.print(
                f"  [yellow]→[/] Domain '{slug}' already existed ({domain_id}); "
                f"back-filled {', '.join(update.keys())}"
            )
        else:
            console.print(f"  [yellow]→[/] Domain '{slug}' already exists ({domain_id})")
        return domain_id

    domain_id = str(uuid.uuid4())
    row = {
        "id": domain_id,
        "name": _sanitize(name),
        "slug": _sanitize(slug),
        "description": _sanitize(description),
        "icon_name": icon_name,
        "sort_order": sort_order,
    }
    if owner_id:
        row["owner_id"] = owner_id
    if org_id:
        row["org_id"] = org_id
    if domain_family:
        row["domain_family"] = _sanitize(domain_family)
    if variant:
        row["variant"] = variant

    _insert_batch(client, "domains", [row])
    console.print(f"  [green]✓[/] Created domain: {name} ({domain_id})")
    return domain_id


def create_curriculum_levels(client: Any, domain_id: str, topics: list[dict]) -> dict[int, str]:
    """Create curriculum_levels rows from suggested_level values. Returns level_number → id."""
    levels_needed = sorted({
        t["suggested_level"] for t in topics if t.get("depth", 0) == 0 and "suggested_level" in t
    })
    if not levels_needed:
        return {}

    existing = _execute(
        "read curriculum levels",
        lambda: client.table("curriculum_levels").select("id, level_number").eq("domain_id", domain_id),
    )
    existing_map = {row["level_number"]: row["id"] for row in existing.data}

    level_map: dict[int, str] = {}
    for level_num in levels_needed:
        if level_num in existing_map:
            level_map[level_num] = existing_map[level_num]
            continue
        level_id = str(uuid.uuid4())
        _insert_batch(client, "curriculum_levels", [{
            "id": level_id,
            "domain_id": domain_id,
            "level_number": level_num,
            "name": LEVEL_NAMES.get(level_num, f"Level {level_num}"),
            "description": LEVEL_DESCRIPTIONS.get(level_num, ""),
        }])
        level_map[level_num] = level_id

    created = len(level_map) - len(existing_map)
    if created > 0:
        console.print(f"  [green]✓[/] {created} curriculum levels created")
    if existing_map:
        console.print(f"  [yellow]→[/] {len(existing_map)} curriculum levels already existed")
    return level_map


def backfill_levels(
    client: Any,
    domain_id: str,
    topics: list[dict],
    level_map: dict[int, str],
    existing_topics: dict[tuple[str, int, str | None], str],
) -> int:
    """Set curriculum_level_id on existing depth-0 topics that are missing it. Returns count updated."""
    updated = 0
    for topic in topics:
        if topic.get("depth", 0) != 0 or "suggested_level" not in topic:
            continue
        level_id = level_map.get(topic["suggested_level"])
        topic_id = existing_topics.get((topic["title"].lower(), 0, None))
        if not level_id or not topic_id:
            continue
        _execute(
            "update topic level",
            lambda: client.table("topics").update({"curriculum_level_id": level_id}).eq("id", topic_id),
        )
        updated += 1
    return updated


def find_existing_topics(
    client: Any, domain_id: str,
) -> tuple[dict[tuple[str, int, str | None], str], dict[str, str]]:
    """Fetch existing topics for a domain.

    Returns (key_to_id, id_to_title) where key is (lower_title, depth, lower_parent_title|None).
    """
    rows = _select_all(
        "read topics",
        lambda: client.table("topics").select("id, title, depth, parent_topic_id").eq("domain_id", domain_id),
    )
    id_to_title: dict[str, str] = {row["id"]: row["title"] for row in rows}
    key_to_id: dict[tuple[str, int, str | None], str] = {}
    for row in rows:
        parent_title = id_to_title.get(row["parent_topic_id"])
        key_to_id[(row["title"].lower(), row["depth"], parent_title.lower() if parent_title else None)] = row["id"]
    return key_to_id, id_to_title


def insert_topics(
    client: Any,
    domain_id: str,
    topics: list[dict],
    parent_id: str | None = None,
    level_map: dict[int, str] | None = None,
    existing_topics: dict[tuple[str, int, str | None], str] | None = None,
    parent_title: str | None = None,
) -> tuple[dict[str, str], int]:
    """Recursively insert topics. Returns (title → id mapping, skipped count).

    Structure leaves may carry ``description`` / ``lesson_id`` (lesson pipeline);
    topics has no column for them so they are ignored here.
    """
    path_to_id: dict[str, str] = {}
    skipped = 0

    for topic in topics:
        depth = topic.get("depth", 0)
        title = topic["title"]

        existing_id = None
        if existing_topics is not None:
            existing_id = existing_topics.get((title.lower(), depth, parent_title.lower() if parent_title else None))

        if existing_id:
            path_to_id[title] = existing_id
            skipped += 1
        else:
            topic_id = str(uuid.uuid4())
            curriculum_level_id = None
            if level_map and depth == 0 and "suggested_level" in topic:
                curriculum_level_id = level_map.get(topic["suggested_level"])
            _insert_batch(client, "topics", [{
                "id": topic_id,
                "book_id": None,  # curriculum topics have no book
                "domain_id": domain_id,
                "parent_topic_id": parent_id,
                "title": _sanitize(title),
                "depth": depth,
                "sort_order": topic.get("sort_order", 0),
                "curriculum_level_id": curriculum_level_id,
            }])
            path_to_id[title] = topic_id
            existing_id = topic_id

        for child in topic.get("children", []):
            child_paths, child_skipped = insert_topics(
                client, domain_id, [child], existing_id, level_map, existing_topics, title,
            )
            path_to_id.update(child_paths)
            skipped += child_skipped

    return path_to_id, skipped


def insert_books(client: Any, domain_id: str, sources: list[dict]) -> dict[str, str]:
    """Create a book record for each manifest source. Returns source_path → book_id."""
    existing = _execute(
        "read books", lambda: client.table("books").select("id, title, file_name").eq("domain_id", domain_id),
    )
    existing_by_title = {row["title"].lower(): row["id"] for row in existing.data if row["title"]}
    existing_by_file = {row["file_name"]: row["id"] for row in existing.data if row["file_name"]}

    source_to_book: dict[str, str] = {}
    created = reused = 0
    for source in sources:
        source_path = source.get("path", "")
        file_name = Path(source_path).name if source_path else None
        title = source.get("title", file_name or "Untitled")

        existing_id = None
        if file_name and file_name in existing_by_file:
            existing_id = existing_by_file[file_name]
        elif title and title.lower() in existing_by_title:
            existing_id = existing_by_title[title.lower()]

        if existing_id:
            source_to_book[source_path] = existing_id
            reused += 1
        else:
            book_id = str(uuid.uuid4())
            _insert_batch(client, "books", [{
                "id": book_id,
                "domain_id": domain_id,
                "title": _sanitize(title),
                "author": _sanitize(source.get("author")),
                "file_name": file_name,
            }])
            source_to_book[source_path] = book_id
            created += 1

    if created:
        console.print(f"  [green]✓[/] {created} books created")
    if reused:
        console.print(f"  [yellow]→[/] {reused} books already existed")
    return source_to_book


# ---------------------------------------------------------------------------
# Objectives, prerequisites, raw-mode exercises
# ---------------------------------------------------------------------------


def insert_learning_objectives(client: Any, path_to_id: dict[str, str], topics: list[dict]) -> int:
    """Insert topic_learning_objectives from structure.json. Returns count inserted."""
    batch: list[dict] = []
    for topic, _ in _walk_topics(topics):
        objectives = topic.get("learning_objectives", [])
        topic_id = _lookup_title(path_to_id, topic["title"]) if objectives else None
        if not topic_id:
            continue
        for i, obj in enumerate(objectives):
            batch.append({
                "id": str(uuid.uuid4()),
                "topic_id": topic_id,
                "objective_text": _sanitize(obj["text"]),
                "bloom_level": _clamp_bloom(obj.get("bloom_level"), "understand"),
                "sort_order": i,
            })
    return _insert_rows(client, "topic_learning_objectives", batch)


def _has_cycle(edges: list[dict]) -> bool:
    """Kahn's algorithm over prerequisite -> topic edges."""
    adj: dict[str, set[str]] = {}
    deg: dict[str, int] = {}
    for edge in edges:
        src, dst = edge["prerequisite_topic_id"], edge["topic_id"]
        if dst not in adj.setdefault(src, set()):
            adj[src].add(dst)
            deg[dst] = deg.get(dst, 0) + 1
        deg.setdefault(src, 0)
    queue = deque(n for n, d in deg.items() if d == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for neighbor in adj.get(node, set()):
            deg[neighbor] -= 1
            if deg[neighbor] == 0:
                queue.append(neighbor)
    return visited < len(deg)


def insert_prerequisites(client: Any, path_to_id: dict[str, str], topics: list[dict]) -> int:
    """Insert topic_prerequisites from structure.json. Returns count inserted (0 when cyclic)."""
    edges: list[dict] = []
    for topic, _ in _walk_topics(topics):
        prereqs = topic.get("prerequisites", [])
        topic_id = _lookup_title(path_to_id, topic["title"]) if prereqs else None
        if not topic_id:
            continue
        for prereq in prereqs:
            prereq_id = _lookup_title(path_to_id, prereq["topic"])
            if not prereq_id or prereq_id == topic_id:
                continue
            edges.append({
                "id": str(uuid.uuid4()),
                "topic_id": topic_id,
                "prerequisite_topic_id": prereq_id,
                "strength": prereq.get("strength", "recommended"),
            })

    if _has_cycle(edges):
        console.print(
            "[yellow]Warning:[/] Prerequisite graph has cycles — "
            "skipping prerequisite insertion to avoid data inconsistency"
        )
        return 0
    return _insert_rows(client, "topic_prerequisites", edges)


def _exercise_chunk_row(topic_id: str, position: int, ex: dict, book_id: str | None) -> dict | None:
    """An exercise stored as a content_chunks row at chunk_index EXERCISE_CHUNK_OFFSET + position."""
    content = _sanitize(ex.get("problem_statement", ""))
    if not content:
        return None
    expected = ex.get("expected_solution") or ""
    meta = {
        "type": "exercise",
        "has_code": "```" in expected,
        "exercise": {
            "title": ex.get("title", ""),
            "hints": ex.get("hints", []),
            "expected_solution": expected,
            "common_mistakes": ex.get("common_mistakes", []),
            "bloom_level": _clamp_bloom(ex.get("bloom_level"), "apply"),
            "difficulty": ex.get("difficulty", 1),
        },
    }
    return {
        "id": str(uuid.uuid4()),
        "topic_id": topic_id,
        "book_id": book_id,
        "chunk_index": EXERCISE_CHUNK_OFFSET + position,
        "content": content,
        "page_number": None,
        "token_count": len(content.split()),  # rough estimate
        "metadata": json.dumps(meta),
    }


def insert_exercises(
    client: Any,
    exercises: list[dict],
    path_to_id: dict[str, str],
    source_to_book: dict[str, str] | None = None,
    update_mode: bool = False,
    replace_chunks: bool = False,
) -> int:
    """Insert exercises.json entries as content_chunks with exercise metadata. Returns count inserted.

    In update mode the exercise chunks follow the rule of the pages: an existing
    (topic, chunk_index) is kept and skipped, or with ``replace_chunks`` the old
    exercise chunks of those topics are deleted first. Without this, every
    ``--update`` run would add each exercise a second time."""
    fallback_book_id = next(iter(source_to_book.values()), None) if source_to_book else None
    batch: list[dict] = []
    for entry in exercises:
        topic_id = None
        for title in reversed(entry.get("topic_path", [])):
            topic_id = _lookup_title(path_to_id, title)
            if topic_id:
                break
        if not topic_id:
            continue
        for j, ex in enumerate(entry.get("exercises", [])):
            row = _exercise_chunk_row(topic_id, j, ex, fallback_book_id)
            if row:
                batch.append(row)

    if update_mode and batch:
        topic_ids = sorted({row["topic_id"] for row in batch})
        if replace_chunks:
            delete_practice_rows(client, topic_ids, {}, include_exercise_chunks=True)
        else:
            existing = fetch_chunk_ids(client, topic_ids)
            batch = [row for row in batch if (row["topic_id"], row["chunk_index"]) not in existing]
    return _insert_rows(client, "content_chunks", batch)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def _ensure_storage_bucket(client: Any) -> None:
    """Ensure the curriculum-images storage bucket exists."""
    try:
        _retry("read storage bucket", lambda: client.storage.get_bucket(CURRICULUM_IMAGES_BUCKET))
    except TransientUploadError:
        raise
    except Exception:  # noqa: BLE001 — storage client raises its own error family
        try:
            _retry("create storage bucket", lambda: client.storage.create_bucket(
                CURRICULUM_IMAGES_BUCKET,
                options={"public": True, "file_size_limit": 10 * 1024 * 1024},
            ))
            console.print(f"  [green]✓[/] Created storage bucket: {CURRICULUM_IMAGES_BUCKET}")
        except TransientUploadError:
            raise
        except Exception as e:  # noqa: BLE001
            if "already exists" not in str(e).lower():
                console.print(f"  [yellow]Warning:[/] Could not create storage bucket: {e}")


def upload_chunk_images(
    client: Any,
    chunks: list[dict],
    domain_slug: str,
    extracted_dir: Path | None = None,
) -> int:
    """Upload images referenced in chunks to Supabase Storage.

    Sets ``url`` on each image entry that was uploaded (or already existed).
    Content rewriting happens separately in ``inline_chunk_images``. Returns
    the count of images uploaded.
    """
    if not any(chunk.get("images") for chunk in chunks):
        return 0

    _ensure_storage_bucket(client)
    bucket = client.storage.from_(CURRICULUM_IMAGES_BUCKET)
    input_dir = extracted_dir.parent if extracted_dir else None

    uploaded = 0
    for chunk in chunks:
        for img in chunk.get("images", []):
            file_path = _image_file(input_dir, img.get("local_path", "")) if input_dir else None
            if not file_path:
                continue
            storage_path = f"{domain_slug}/{img['id']}{file_path.suffix}"
            data = file_path.read_bytes()
            options = {"content-type": img.get("mime_type", "image/png")}
            try:
                _retry(
                    f"upload image {img['id']}",
                    lambda path=storage_path, data=data, options=options: bucket.upload(path, data, options),
                )
            except TransientUploadError:
                raise  # storage is unreachable: stop here instead of failing image after image
            except Exception as e:  # noqa: BLE001 — storage errors are not a single class
                if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                    console.print(f"    [yellow]Warning:[/] Failed to upload {img['id']}: {e}")
                    continue
            else:
                uploaded += 1
            img["url"] = bucket.get_public_url(storage_path)
    return uploaded


def inline_chunk_images(chunks: list[dict]) -> list[str]:
    """Rewrite ``![alt](image:<id>)`` placeholders to the uploaded URL.

    Chunks that have images but no placeholder keep the legacy behaviour: the
    image markdown is appended at the end of the content. Returns the list of
    unresolved placeholders as ``"<topic> #<chunk_index>: <id>"`` strings.
    """
    unresolved: list[str] = []
    for chunk in chunks:
        images = chunk.get("images") or []
        by_id = {img["id"]: img for img in images if img.get("id") and img.get("url")}
        content = chunk.get("content", "")
        topic = (chunk.get("topic_path") or ["?"])[-1]

        if IMAGE_PLACEHOLDER_RE.search(content):
            def replace(match: Any) -> str:
                image = by_id.get(match.group("id"))
                if image is None:
                    unresolved.append(f"{topic} #{chunk.get('chunk_index', 0)}: {match.group('id')}")
                    return match.group(0)
                alt = match.group("alt") or image.get("alt_text", "")
                return f"![{alt}]({image['url']})"

            chunk["content"] = IMAGE_PLACEHOLDER_RE.sub(replace, content)
        elif by_id:
            chunk["content"] = content + "".join(
                f"\n\n![{img.get('alt_text', '')}]({img['url']})\n\n" for img in by_id.values()
            )
    return unresolved


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


@dataclass
class ChunkInsertResult:
    """Counts plus the (topic_id, chunk_index) -> chunk_id map of every chunk now in the DB
    for the uploaded topics (inserted and, in update mode, kept)."""

    inserted: int = 0
    skipped: int = 0
    replaced: int = 0
    chunk_ids: dict[tuple[str, int], str] = field(default_factory=dict)

    def __iter__(self) -> Iterator[int]:
        """Tuple compatibility: ``inserted, skipped, replaced = insert_chunks(...)``."""
        yield from (self.inserted, self.skipped, self.replaced)


def fetch_chunk_ids(client: Any, topic_ids: list[str]) -> dict[tuple[str, int], str]:
    """(topic_id, chunk_index) -> chunk_id for existing chunks of the given topics."""
    out: dict[tuple[str, int], str] = {}
    for i in range(0, len(topic_ids), BATCH_SIZE):
        sub_ids = topic_ids[i:i + BATCH_SIZE]
        rows = _select_all(
            "read chunk ids",
            lambda: client.table("content_chunks").select("id, topic_id, chunk_index").in_("topic_id", sub_ids),
        )
        for row in rows:
            out[(row["topic_id"], row["chunk_index"])] = row["id"]
    return out


def insert_chunks(
    client: Any,
    chunks: list[dict],
    path_to_id: dict[str, str],
    source_to_book: dict[str, str] | None = None,
    update_mode: bool = False,
    replace_chunks: bool = False,
) -> ChunkInsertResult:
    """Insert content chunks, matching each to its topic by the most specific title.

    Chunk ``metadata`` is merged with ``has_code`` and the uploaded image URLs
    and stored as a JSON string. Image placeholders must already have been
    rewritten (``inline_chunk_images``).
    """
    result = ChunkInsertResult()
    fallback_book_id = next(iter(source_to_book.values())) if source_to_book else None

    batch: list[dict] = []
    for chunk in chunks:
        content = chunk.get("content", "").strip()
        if not content:
            result.skipped += 1
            continue
        topic_id = None
        for title in reversed(chunk.get("topic_path", [])):
            topic_id = _lookup_title(path_to_id, title)
            if topic_id:
                break
        if not topic_id:
            result.skipped += 1
            continue

        book_id = fallback_book_id
        chunk_source = chunk.get("_source", "")
        if source_to_book and chunk_source in source_to_book:
            book_id = source_to_book[chunk_source]

        meta: dict[str, Any] = {"has_code": chunk.get("has_code", False)}
        extra = chunk.get("metadata", {})
        if isinstance(extra, dict):
            meta.update(extra)
        chunk_images = chunk.get("images", [])
        if chunk_images:
            meta["images"] = [
                {k: v for k, v in img.items() if k in ("url", "alt_text", "id")}
                for img in chunk_images
                if img.get("url")
            ]

        batch.append({
            "id": str(uuid.uuid4()),
            "topic_id": topic_id,
            "book_id": book_id,
            "chunk_index": chunk.get("chunk_index", 0),
            "content": _sanitize(content),
            "page_number": chunk.get("page_number"),
            "token_count": chunk.get("token_count"),
            "metadata": json.dumps(meta),
        })

    if update_mode and batch:
        topic_ids = list({row["topic_id"] for row in batch})
        existing = fetch_chunk_ids(client, topic_ids)
        topics_with_chunks = {topic_id for topic_id, _ in existing}

        if replace_chunks and topics_with_chunks:
            for tid in topics_with_chunks:
                _execute("delete old chunks", lambda: client.table("content_chunks").delete().eq("topic_id", tid))
            result.replaced = len(existing)
            existing = {}
            console.print(f"    [yellow]↻[/] Replacing chunks for {len(topics_with_chunks)} topics")

        if existing:
            original_len = len(batch)
            batch = [row for row in batch if (row["topic_id"], row["chunk_index"]) not in existing]
            result.skipped += original_len - len(batch)
            result.chunk_ids.update(existing)

    result.inserted = _insert_rows(client, "content_chunks", batch)
    for row in batch:
        result.chunk_ids[(row["topic_id"], row["chunk_index"])] = row["id"]

    if result.skipped:
        console.print(f"    [yellow]⚠[/] {result.skipped} chunks skipped (empty, unmatched, or existing)")
    return result


# ---------------------------------------------------------------------------
# Lesson-mode practice content
# ---------------------------------------------------------------------------


@dataclass
class PracticeRows:
    """Rows planned for the practice tables (and the exercise-chunk fallback)."""

    reading_questions: list[dict] = field(default_factory=list)
    topic_questions: list[dict] = field(default_factory=list)
    topic_cards: list[dict] = field(default_factory=list)
    topic_exercises: list[dict] = field(default_factory=list)
    exercise_chunks: list[dict] = field(default_factory=list)
    topic_ids: set[str] = field(default_factory=set)

    def counts(self) -> dict[str, int]:
        return {
            "reading_questions": len(self.reading_questions),
            "topic_questions": len(self.topic_questions),
            "topic_cards": len(self.topic_cards),
            "topic_exercises": len(self.topic_exercises),
            "exercise_chunks": len(self.exercise_chunks),
        }


def build_practice_rows(
    lesson: LessonPractice,
    topic_id: str,
    chunk_for_page: Callable[[int | None], str | None],
    use_exercise_table: bool,
    book_id: str | None = None,
) -> PracticeRows:
    """Map one lesson's questions/cards files to table rows (docs/artifacts.md, Upload mapping)."""
    rows = PracticeRows(topic_ids={topic_id})
    questions = lesson.questions or {}

    for q in questions.get("embedded") or []:
        kind = q.get("kind")
        page_index = q.get("page_index")
        rows.reading_questions.append({
            "id": str(uuid.uuid4()),
            "chunk_id": chunk_for_page(page_index),
            "topic_id": topic_id,
            "question_type": kind,
            "question_text": _sanitize(q.get("prompt")),
            "expected_answer": _sanitize(q.get("expected_answer")),
            "position": q.get("position") or EMBEDDED_DEFAULT_POSITION.get(kind, "after"),
            "sort_order": page_index if isinstance(page_index, int) else 0,
        })

    for i, q in enumerate(questions.get("quiz") or []):
        bloom = _clamp_bloom(q.get("bloom_level"), "understand")
        page_index = q.get("page_index")
        rows.topic_questions.append({
            "id": str(uuid.uuid4()),
            "topic_id": topic_id,
            "chunk_id": chunk_for_page(page_index) if page_index is not None else None,
            "question_type": q.get("type"),
            "bloom_level": bloom,
            "mastery_level": q.get("mastery_level") or BLOOM_TO_MASTERY[bloom],
            "question_text": _sanitize(q.get("prompt")),
            "explanation": _sanitize(q.get("explanation")),
            "sort_order": i,
            "payload": {key: q.get(key) for key in TOPIC_QUESTION_PAYLOAD_KEYS},
        })

    for i, card in enumerate((lesson.cards or {}).get("cards") or []):
        page_index = card.get("page_index")
        rows.topic_cards.append({
            "id": str(uuid.uuid4()),
            "topic_id": topic_id,
            "chunk_id": chunk_for_page(page_index) if page_index is not None else None,
            "card_type": card.get("card_type"),
            "front": _sanitize(card.get("front")),
            "back": _sanitize(card.get("back")),
            "card_data": card.get("card_data") or {},
            "source_reference": _sanitize(card.get("source_reference")),
            "sort_order": i,
        })

    for j, ex in enumerate(questions.get("exercises") or []):
        if use_exercise_table:
            rows.topic_exercises.append({
                "id": str(uuid.uuid4()),
                "topic_id": topic_id,
                "title": _sanitize(ex.get("title", "")),
                "problem_statement": _sanitize(ex.get("problem_statement", "")),
                "hints": ex.get("hints", []),
                "expected_solution": _sanitize(ex.get("expected_solution", "")),
                "common_mistakes": ex.get("common_mistakes", []),
                "bloom_level": _clamp_bloom(ex.get("bloom_level"), "apply"),
                "difficulty": ex.get("difficulty", 1),
                "sort_order": j,
            })
        else:
            row = _exercise_chunk_row(topic_id, j, ex, book_id)
            if row:
                rows.exercise_chunks.append(row)
    return rows


def plan_practice_rows(
    practice: PracticeContent,
    topics: list[dict],
    path_to_id: Mapping[str, str],
    chunk_ids: Mapping[tuple[str, int], str],
    tables: Mapping[str, bool],
    book_id: str | None = None,
) -> PracticeRows:
    """Resolve every lesson to its topic and chunk ids and build all practice rows.

    Lessons whose topic cannot be resolved are skipped with a warning (check()
    reports them as errors before any write happens).
    """
    rows = PracticeRows()
    lesson_to_title = _lesson_titles(topics)
    use_exercise_table = bool(tables.get("topic_exercises"))
    for lesson in practice.lessons.values():
        title = lesson_to_title.get(lesson.lesson_id) or lesson.title
        topic_id = _lookup_title(path_to_id, title)
        if not topic_id:
            console.print(f"  [yellow]Warning:[/] lesson {lesson.lesson_id!r} matches no topic — practice content skipped")
            continue

        def chunk_for_page(page_index: int | None, _tid: str = topic_id) -> str | None:
            return chunk_ids.get((_tid, page_index)) if isinstance(page_index, int) else None

        lesson_rows = build_practice_rows(lesson, topic_id, chunk_for_page, use_exercise_table, book_id)
        rows.reading_questions += lesson_rows.reading_questions
        rows.topic_questions += lesson_rows.topic_questions
        rows.topic_cards += lesson_rows.topic_cards
        rows.topic_exercises += lesson_rows.topic_exercises
        rows.exercise_chunks += lesson_rows.exercise_chunks
        rows.topic_ids |= lesson_rows.topic_ids
    return rows


def insert_practice_rows(client: Any, rows: PracticeRows, tables: Mapping[str, bool]) -> dict[str, int]:
    """Insert planned practice rows into every present table; returns counts per key."""
    counts = {key: 0 for key in rows.counts()}
    for table in PRACTICE_TABLES:
        if tables.get(table):
            counts[table] = _insert_rows(client, table, getattr(rows, table))
    counts["exercise_chunks"] = _insert_rows(client, "content_chunks", rows.exercise_chunks)
    return counts


def delete_practice_rows(
    client: Any,
    topic_ids: list[str],
    tables: Mapping[str, bool],
    include_exercise_chunks: bool = False,
) -> dict[str, int]:
    """Delete practice rows of the given topics from every present table (+ exercise chunks)."""
    deleted = {table: 0 for table in PRACTICE_TABLES}
    deleted["exercise_chunks"] = 0
    for i in range(0, len(topic_ids), BATCH_SIZE):
        sub_ids = topic_ids[i:i + BATCH_SIZE]
        for table in PRACTICE_TABLES:
            if tables.get(table):
                result = _execute(
                    f"delete old {table}", lambda: client.table(table).delete().in_("topic_id", sub_ids),
                )
                deleted[table] += len(result.data or [])
        if include_exercise_chunks:
            result = _execute(
                "delete old exercise chunks",
                lambda: client.table("content_chunks").delete().in_("topic_id", sub_ids)
                .gte("chunk_index", EXERCISE_CHUNK_OFFSET),
            )
            deleted["exercise_chunks"] += len(result.data or [])
    return deleted


# ---------------------------------------------------------------------------
# Upload result + plan printing
# ---------------------------------------------------------------------------


def build_upload_result(
    *,
    domain_id: str | None,
    target: Target,
    owner_type: str,
    owner_id: str | None,
    update_mode: str,
    skipped_tables: list[str],
    topics_inserted: int = 0,
    topics_skipped: int = 0,
    chunks_inserted: int = 0,
    chunks_skipped: int = 0,
    chunks_replaced: int = 0,
    practice_counts: Mapping[str, int] | None = None,
    exercise_chunks: int = 0,
) -> dict:
    """The upload_result.json document (keys per docs/artifacts.md, Upload mapping)."""
    counts = dict(practice_counts or {})
    return {
        "domain_id": domain_id,
        "target": target.name,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "topics_inserted": topics_inserted,
        "topics_skipped": topics_skipped,
        "chunks_inserted": chunks_inserted,
        "chunks_skipped": chunks_skipped,
        "chunks_replaced": chunks_replaced,
        "reading_questions": counts.get("reading_questions", 0),
        "topic_questions": counts.get("topic_questions", 0),
        "topic_cards": counts.get("topic_cards", 0),
        "topic_exercises": counts.get("topic_exercises", 0),
        "exercise_chunks": exercise_chunks + counts.get("exercise_chunks", 0),
        "skipped_tables": list(skipped_tables),
        "update_mode": update_mode,
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "contract_version": CONTRACT_VERSION,
    }


def _print_plan(
    target: Target,
    owner_type: str,
    owner_id: str | None,
    slug: str,
    row_counts: Mapping[str, int],
    skipped_tables: list[str],
) -> None:
    console.print("\n[bold]Dry run — nothing will be written.[/]")
    console.print(f"  Target: {target.host} ({target.name})")
    console.print(f"  Owner:  {owner_type} {owner_id or '(none)'}")
    console.print(f"  Domain: {slug}")
    for table, count in row_counts.items():
        note = "  (table missing — skipped)" if table in skipped_tables else ""
        console.print(f"  {table:<28}{count:>6}{note}")
    if skipped_tables:
        console.print(f"  skipped_tables: {skipped_tables}")


def _mode_name(update_mode: bool, replace_chunks: bool) -> str:
    if replace_chunks:
        return "replace_chunks"
    return "update" if update_mode else "insert"


# ---------------------------------------------------------------------------
# Upload / enrich flows
# ---------------------------------------------------------------------------


@dataclass
class _RunState:
    """What a run has done so far, read when it stops on a TransientUploadError."""

    writing: bool = False  # True once the run may have written to the database


def _topic_keys(topics: list[dict]) -> Iterator[tuple[str, int, str | None]]:
    """The (lower title, depth, lower parent title) key of every topic, as find_existing_topics builds it."""
    for topic, path in _walk_topics(topics):
        parent = path[-2].lower() if len(path) > 1 else None
        yield topic["title"].lower(), topic.get("depth", 0), parent


def _refuse_duplicate_upload(client: Any, slug: str, topics: list[dict]) -> None:
    """Plain (non ``--update``) upload: stop when the domain already holds topics of this curriculum.

    That is a second upload of the same curriculum, usually after an interrupted
    first one. Inserting every topic again would duplicate them, since nothing in
    the schema keeps topic titles unique within a domain.
    """
    found = _execute("read domain", lambda: client.table("domains").select("id").eq("slug", slug)).data
    if not found:
        return
    existing, _ = find_existing_topics(client, found[0]["id"])
    keys = list(_topic_keys(topics))
    already = sum(1 for key in keys if key in existing)
    if already:
        raise UploadError(
            f"Domain '{slug}' already holds {already} of the {len(keys)} topics of this curriculum "
            "(an earlier upload, possibly an interrupted one). A plain upload would insert them a "
            "second time. Run the same command with --update to add what is missing, or with "
            "--update --replace-chunks to also replace the pages.",
            1,
        )


def _recorded_result(input_dir: Path) -> dict:
    """The upload_result.json document, or {} when it is absent or unreadable."""
    path = Path(input_dir) / UPLOAD_RESULT_FILE
    if not path.exists():
        return {}
    try:
        data = load_json(path)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _forget_stale_result(input_dir: Path, state: _RunState, target: Target) -> bool:
    """Remove ``upload_result.json`` when an upload to the target it records stopped
    after it began writing. True if removed.

    The file records an earlier, finished upload, and status.py reads its mere
    presence as "uploaded" while that target now holds a partial state. The run
    that finishes the upload writes it again. A record of another target is
    kept: this run did not touch that upload. ``enrich`` never calls this: it
    reads the domain id from the file, and needs it again to finish.
    """
    if not state.writing or _recorded_result(input_dir).get("target") != target.name:
        return False
    (Path(input_dir) / UPLOAD_RESULT_FILE).unlink()
    return True


def _stopped(
    error: TransientUploadError,
    state: _RunState,
    incomplete: str = "",
    resume: str = "",
    result_removed: bool = False,
) -> TransientUploadError:
    """``error`` with what the stopped run left behind and how to finish it."""
    if state.writing:
        advice = f"{incomplete} {resume} Rows already written are reused, not duplicated."
        if result_removed:
            advice += f" The earlier {UPLOAD_RESULT_FILE} was removed, so this upload does not show as done."
    else:
        advice = "Nothing was written to the database. Run the same command again."
    return TransientUploadError(f"{error}.\n{advice}", error.exit_code)


def _note_stale_result_removed() -> None:
    console.print(f"  [yellow]→[/] {UPLOAD_RESULT_FILE} removed: this run stopped after it began writing")


def upload_curriculum(
    input_dir: Path,
    client: Any,
    target: Target,
    owner_type: str = "user",
    user_id: str | None = None,
    org_id: str | None = None,
    update_mode: bool = False,
    replace_chunks: bool = False,
    skip_validation: bool = False,
    dry_run: bool = False,
) -> dict:
    """Upload a curriculum from an output directory. Returns the upload_result document
    (written to ``<input_dir>/upload_result.json`` unless ``dry_run``).

    Requests that fail for a passing reason are retried (``_retry``). When one
    still fails, the run stops with a TransientUploadError that says whether the
    database was touched and which command finishes the upload."""
    state = _RunState()
    try:
        return _upload_curriculum(
            input_dir, client, target, state, owner_type=owner_type, user_id=user_id, org_id=org_id,
            update_mode=update_mode, replace_chunks=replace_chunks, skip_validation=skip_validation,
            dry_run=dry_run,
        )
    except TransientUploadError as e:
        removed = _forget_stale_result(input_dir, state, target)
        if update_mode:
            raise _stopped(
                e, state, "The update is incomplete.", "Run the same command again to finish it.", removed,
            ) from e
        raise _stopped(
            e, state, "The curriculum may be only partly uploaded.",
            "Run the same command again with --update --replace-chunks to finish it.", removed,
        ) from e
    except BaseException:  # any other stop, Ctrl-C included: the old result file is stale too
        if _forget_stale_result(input_dir, state, target):
            _note_stale_result_removed()
        raise


def _upload_curriculum(
    input_dir: Path,
    client: Any,
    target: Target,
    state: _RunState,
    *,
    owner_type: str,
    user_id: str | None,
    org_id: str | None,
    update_mode: bool,
    replace_chunks: bool,
    skip_validation: bool,
    dry_run: bool,
) -> dict:
    input_dir = Path(input_dir)
    _validate_or_exit(input_dir, "upload", skip_validation)
    inputs = _gate_or_raise(input_dir)
    manifest, topics, chunks = inputs.manifest, inputs.topics, inputs.chunks
    lesson_mode = _is_lesson_mode(input_dir)
    practice = load_practice_content(input_dir) if lesson_mode else PracticeContent()

    name = manifest.get("name", input_dir.name)
    slug = _slug_of(manifest, input_dir)
    description = manifest.get("description", "")
    sort_order = manifest.get("sort_order", 99)
    # Every domain gets a family and a variant, even if the manifest omitted
    # them: a standalone "extensive" curriculum IS its own family, so later
    # condensed variants join it instead of leaving it orphaned (migration 029).
    domain_family = manifest.get("domain_family") or slug
    variant = manifest.get("variant") or "extensive"
    owner_id = user_id if owner_type == "user" else org_id

    console.print(f"\n[bold blue]Uploading curriculum:[/] {name}")
    console.print(f"  Domain: {slug}")
    console.print(f"  Variant: {variant} (family: {domain_family})")
    console.print(f"  Pipeline: {'lesson' if lesson_mode else 'raw'}")
    console.print(f"  Topics: {_count_topics(topics)}")
    console.print(f"  Chunks: {len(chunks)}")

    tables = probe_practice_tables(client) if lesson_mode else {}
    skipped_tables = [t for t in PRACTICE_TABLES if lesson_mode and not tables.get(t)]

    exercises_path = input_dir / "exercises.json"
    raw_exercises = load_json(exercises_path) if exercises_path.exists() else []

    if not update_mode:
        _refuse_duplicate_upload(client, slug, topics)  # reads only: `plan` reports the refusal too

    if dry_run:
        planned = plan_practice_rows(practice, topics, {t["title"]: t["title"] for t, _ in _walk_topics(topics)}, {}, tables)
        row_counts = {
            "domains": 1,
            "topics": _count_topics(topics),
            "content_chunks": sum(1 for c in chunks if c.get("content", "").strip()),
            "images": sum(len(c.get("images") or []) for c in chunks),
            "topic_learning_objectives": sum(len(t.get("learning_objectives") or []) for t, _ in _walk_topics(topics)),
            "topic_prerequisites": sum(len(t.get("prerequisites") or []) for t, _ in _walk_topics(topics)),
            **planned.counts(),
        }
        row_counts["exercise_chunks"] += sum(len(e.get("exercises") or []) for e in raw_exercises)
        _print_plan(target, owner_type, owner_id, slug, row_counts, skipped_tables)
        return build_upload_result(
            domain_id=None, target=target, owner_type=owner_type, owner_id=owner_id,
            update_mode=_mode_name(update_mode, replace_chunks), skipped_tables=skipped_tables,
            practice_counts=planned.counts(),
        )

    # Images go first: a failed image stops the run before the database holds
    # half a curriculum. Storage objects left behind are reused by the next run.
    extracted_dir = input_dir / "extracted"
    images_uploaded = upload_chunk_images(
        client, chunks, slug, extracted_dir=extracted_dir if extracted_dir.exists() else None,
    )
    if images_uploaded:
        console.print(f"  [green]✓[/] {images_uploaded} images uploaded to Storage")
    unresolved = inline_chunk_images(chunks)
    if unresolved:
        raise UploadError(
            "Image placeholders without an uploaded image (validate should have caught this); "
            "nothing was written to the database:\n  " + "\n  ".join(unresolved),
            1,
        )

    state.writing = True
    domain_id = create_domain(
        client, name=name, slug=slug, description=description, owner_id=user_id,
        org_id=org_id if owner_type == "org" else None, sort_order=sort_order,
        domain_family=domain_family, variant=variant,
    )

    console.print("  Creating curriculum levels...")
    level_map = create_curriculum_levels(client, domain_id, topics)

    existing_topics = None
    if update_mode:
        existing_topics, _ = find_existing_topics(client, domain_id)
        console.print(f"  [yellow]Update mode:[/] {len(existing_topics)} existing topics found")
        if level_map and existing_topics:
            backfilled = backfill_levels(client, domain_id, topics, level_map, existing_topics)
            if backfilled:
                console.print(f"  [green]✓[/] {backfilled} existing topics assigned to curriculum levels")

    sources = manifest.get("sources", [])
    source_to_book: dict[str, str] = {}
    if sources:
        console.print("  Creating books...")
        source_to_book = insert_books(client, domain_id, sources)
    fallback_book_id = next(iter(source_to_book.values()), None)

    console.print("  Inserting topics...")
    path_to_id, topics_skipped = insert_topics(
        client, domain_id, topics, level_map=level_map, existing_topics=existing_topics,
    )
    topics_inserted = len(path_to_id) - topics_skipped
    console.print(f"  [green]✓[/] {topics_inserted} topics inserted")
    if topics_skipped:
        console.print(f"  [yellow]→[/] {topics_skipped} topics already existed")

    console.print("  Inserting chunks...")
    chunk_result = insert_chunks(
        client, chunks, path_to_id, source_to_book, update_mode=update_mode, replace_chunks=replace_chunks,
    )
    console.print(f"  [green]✓[/] {chunk_result.inserted} chunks inserted")
    if chunk_result.replaced:
        console.print(f"  [yellow]↻[/] {chunk_result.replaced} chunks replaced")

    if update_mode and existing_topics:
        # Only this curriculum's topics: a domain may also hold another curriculum,
        # whose objectives and prerequisite links are not ours to clear.
        own_existing = {existing_topics[key] for key in _topic_keys(topics) if key in existing_topics}
        cleared = clear_objectives_and_prerequisites(client, own_existing)
        if any(cleared.values()):
            console.print(f"  [yellow]↻[/] Cleared {cleared['objectives']} objectives and "
                          f"{cleared['prerequisites']} prerequisite links of existing topics (re-inserted below)")
    if any(t.get("learning_objectives") for t, _ in _walk_topics(topics)):
        console.print("  Inserting learning objectives...")
        console.print(f"  [green]✓[/] {insert_learning_objectives(client, path_to_id, topics)} objectives inserted")
    if any(t.get("prerequisites") for t, _ in _walk_topics(topics)):
        console.print("  Inserting prerequisite links...")
        console.print(f"  [green]✓[/] {insert_prerequisites(client, path_to_id, topics)} prerequisite links inserted")

    exercise_chunks = 0
    if raw_exercises:
        console.print("  Inserting exercises...")
        exercise_chunks = insert_exercises(
            client, raw_exercises, path_to_id, source_to_book, update_mode=update_mode, replace_chunks=replace_chunks,
        )
        console.print(f"  [green]✓[/] {exercise_chunks} exercises inserted")

    practice_counts: dict[str, int] = {}
    if practice.lessons:
        rows = plan_practice_rows(practice, topics, path_to_id, chunk_result.chunk_ids, tables, fallback_book_id)
        if update_mode and existing_topics:
            stale = sorted(rows.topic_ids & set(existing_topics.values()))
            if stale:
                deleted = delete_practice_rows(client, stale, tables, include_exercise_chunks=not tables.get("topic_exercises"))
                console.print(f"  [yellow]↻[/] Cleared practice rows of {len(stale)} existing topics: {deleted}")
        console.print("  Inserting practice content...")
        practice_counts = insert_practice_rows(client, rows, tables)
        console.print(
            f"  [green]✓[/] {practice_counts['reading_questions']} reading questions, "
            f"{practice_counts['topic_questions']} quiz questions, {practice_counts['topic_cards']} cards, "
            f"{practice_counts['topic_exercises']} exercises, {practice_counts['exercise_chunks']} exercise chunks"
        )

    result = build_upload_result(
        domain_id=domain_id, target=target, owner_type=owner_type, owner_id=owner_id,
        update_mode=_mode_name(update_mode, replace_chunks), skipped_tables=skipped_tables,
        topics_inserted=topics_inserted, topics_skipped=topics_skipped,
        chunks_inserted=chunk_result.inserted, chunks_skipped=chunk_result.skipped,
        chunks_replaced=chunk_result.replaced, practice_counts=practice_counts, exercise_chunks=exercise_chunks,
    )
    dump_json(input_dir / UPLOAD_RESULT_FILE, result)
    console.print(f"\n  [bold green]Done![/] Domain ID: {domain_id}  ({UPLOAD_RESULT_FILE} written)")
    return result


def _resolve_domain_id(client: Any, input_dir: Path, manifest: dict, target: Target | None = None) -> str | None:
    """Resolve the domain ID for an existing curriculum.

    Checks (in order): upload_result.json, manifest exported_domain_id, slug lookup.
    upload_result.json records the last upload only, so with ``target`` its domain
    id is used only when the file was written for that target: a staging id means
    nothing on prod, where it would match no topic and enrich nothing.
    """
    recorded = _recorded_result(input_dir)
    if recorded.get("domain_id") and (target is None or recorded.get("target") == target.name):
        return recorded["domain_id"]
    domain_id = manifest.get("exported_domain_id")
    if domain_id:
        return domain_id
    slug = _slug_of(manifest, input_dir)  # the slug upload_curriculum created the domain under
    if slug:
        result = _execute("read domain", lambda: client.table("domains").select("id").eq("slug", slug))
        if result.data:
            return result.data[0]["id"]
    return None


def clear_objectives_and_prerequisites(client: Any, topic_ids: Iterable[str]) -> dict[str, int]:
    """Delete the learning objectives and outgoing prerequisite links of `topic_ids`.

    Update mode and enrich re-insert both from structure.json; without this a re-run
    duplicates the objectives and aborts on topic_prerequisites UNIQUE(topic_id,
    prerequisite_topic_id). Returns counts of deleted rows."""
    ids = sorted(set(topic_ids))
    deleted = {"objectives": 0, "prerequisites": 0}
    for i in range(0, len(ids), BATCH_SIZE):
        sub_ids = ids[i:i + BATCH_SIZE]
        result = _execute(
            "delete old objectives",
            lambda: client.table("topic_learning_objectives").delete().in_("topic_id", sub_ids),
        )
        deleted["objectives"] += len(result.data or [])
        result = _execute(
            "delete old prerequisites",
            lambda: client.table("topic_prerequisites").delete().in_("topic_id", sub_ids),
        )
        deleted["prerequisites"] += len(result.data or [])
    return deleted


def clear_enrichment_data(client: Any, domain_id: str, tables: Mapping[str, bool] | None = None) -> dict[str, int]:
    """Delete all enrichment data of a domain: objectives, prerequisites, exercise chunks
    and, for every present practice table, its rows. Returns counts of deleted items."""
    rows = _select_all("read topics", lambda: client.table("topics").select("id").eq("domain_id", domain_id))
    topic_ids = [row["id"] for row in rows]
    deleted = {"objectives": 0, "prerequisites": 0, "exercises": 0}
    if not topic_ids:
        return deleted

    deleted.update(clear_objectives_and_prerequisites(client, topic_ids))
    practice_deleted = delete_practice_rows(client, topic_ids, tables or {}, include_exercise_chunks=True)
    deleted["exercises"] = practice_deleted.pop("exercise_chunks")
    deleted.update(practice_deleted)
    return deleted


def enrich_curriculum(
    input_dir: Path,
    client: Any,
    target: Target,
    skip_validation: bool = False,
) -> dict:
    """Enrich an already-uploaded curriculum with objectives, prerequisites, exercises and
    (lesson mode) practice content. Does NOT create domains, topics or content chunks;
    clears the old enrichment rows first so it is idempotent, which also makes
    running it again the way to finish an enrich that stopped part-way."""
    state = _RunState()
    try:
        return _enrich_curriculum(input_dir, client, target, state, skip_validation=skip_validation)
    except TransientUploadError as e:
        raise _stopped(
            e, state,
            "The enrichment is incomplete: old objectives, prerequisites and practice rows may already be cleared.",
            "Run the same command again to finish it.",
        ) from e


def _enrich_curriculum(
    input_dir: Path,
    client: Any,
    target: Target,
    state: _RunState,
    *,
    skip_validation: bool,
) -> dict:
    input_dir = Path(input_dir)
    _validate_or_exit(input_dir, "enrich", skip_validation)

    manifest_path = input_dir / "manifest.json"
    structure_path = input_dir / "structure.json"
    for path in (manifest_path, structure_path):
        if not path.exists():
            raise UploadError(f"{path.name} not found in {input_dir}", 1)
    manifest = load_json(manifest_path)
    topics = _topics_of(load_json(structure_path))
    lesson_mode = _is_lesson_mode(input_dir)
    practice = load_practice_content(input_dir) if lesson_mode else PracticeContent()
    if practice.lessons:
        _gate_or_raise(input_dir)  # needs chunks.json too, to link practice rows to pages

    console.print(f"\n[bold blue]Enriching curriculum:[/] {manifest.get('name', input_dir.name)}")
    domain_id = _resolve_domain_id(client, input_dir, manifest, target)
    if not domain_id:
        raise UploadError("Could not resolve domain ID. Ensure the curriculum has been uploaded.", 1)
    console.print(f"  Domain ID: {domain_id}")

    existing_topics, id_to_title = find_existing_topics(client, domain_id)
    path_to_id = {id_to_title.get(tid, lower): tid for (lower, _d, _p), tid in existing_topics.items()}
    console.print(f"  Found {len(path_to_id)} existing topics")

    tables = probe_practice_tables(client) if lesson_mode else {}
    skipped_tables = [t for t in PRACTICE_TABLES if lesson_mode and not tables.get(t)]

    console.print("  Clearing existing enrichment data...")
    state.writing = True
    cleared = clear_enrichment_data(client, domain_id, tables)
    if any(v > 0 for v in cleared.values()):
        console.print(f"    Cleared: {cleared}")

    objectives_inserted = prerequisites_inserted = exercise_chunks = 0
    if any(t.get("learning_objectives") for t, _ in _walk_topics(topics)):
        console.print("  Inserting learning objectives...")
        objectives_inserted = insert_learning_objectives(client, path_to_id, topics)
        console.print(f"  [green]✓[/] {objectives_inserted} objectives inserted")
    if any(t.get("prerequisites") for t, _ in _walk_topics(topics)):
        console.print("  Inserting prerequisite links...")
        prerequisites_inserted = insert_prerequisites(client, path_to_id, topics)
        console.print(f"  [green]✓[/] {prerequisites_inserted} prerequisite links inserted")
    exercises_path = input_dir / "exercises.json"
    if exercises_path.exists():
        console.print("  Inserting exercises...")
        exercise_chunks = insert_exercises(client, load_json(exercises_path), path_to_id)
        console.print(f"  [green]✓[/] {exercise_chunks} exercises inserted")

    practice_counts: dict[str, int] = {}
    if practice.lessons:
        chunk_ids = fetch_chunk_ids(client, list(path_to_id.values()))
        rows = plan_practice_rows(practice, topics, path_to_id, chunk_ids, tables)
        console.print("  Inserting practice content...")
        practice_counts = insert_practice_rows(client, rows, tables)
        console.print(f"  [green]✓[/] practice content: {practice_counts}")

    result = build_upload_result(
        domain_id=domain_id, target=target, owner_type="existing", owner_id=None,
        update_mode="enrich", skipped_tables=skipped_tables, topics_skipped=len(path_to_id),
        practice_counts=practice_counts, exercise_chunks=exercise_chunks,
    )
    dump_json(input_dir / UPLOAD_RESULT_FILE, result)
    console.print(
        f"\n  [bold green]Done![/] Domain ID: {domain_id}\n  Enriched: {objectives_inserted} objectives, "
        f"{prerequisites_inserted} prerequisites, {exercise_chunks} exercise chunks"
    )
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_target_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", required=True, choices=TARGETS,
                        help="Database target (required): default=SUPABASE_URL, staging=STAGING_SUPABASE_URL, custom=--db-url")
    parser.add_argument("--db-url", help="Custom database URL (target custom)")
    parser.add_argument("--db-key", help="Custom database service key (target custom)")
    parser.add_argument("--skip-validation", action="store_true",
                        help="Skip the pre-flight validate.py checks (errors normally block)")


def _add_upload_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--owner", default="user", choices=["user", "org"], help="Ownership type")
    parser.add_argument("--user-id", help="Owner user ID (UUID); exactly one of --user-id/--user-email with --owner user")
    parser.add_argument("--user-email", help="Owner email, resolved to a UUID on the target")
    parser.add_argument("--org-id", help="Organization ID (UUID, required if --owner org)")
    parser.add_argument("--update", action="store_true",
                        help="Incremental update: skip existing topics, add only new ones")
    parser.add_argument("--replace-chunks", action="store_true",
                        help="With --update: replace chunk content (and practice rows) for existing topics")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan (row counts per table, target, owner) and write nothing")


def _build_parsers() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    """(legacy flag parser, subcommand parser)."""
    legacy = argparse.ArgumentParser(description="Upload curriculum to Supabase")
    legacy.add_argument("--input", required=True, help="Path to curriculum output directory")
    _add_target_flags(legacy)
    _add_upload_flags(legacy)
    legacy.add_argument("--enrich", action="store_true",
                        help="Enrich an existing curriculum (objectives, prerequisites, exercises, practice "
                             "content); idempotent: clears old enrichment data first")

    sub = argparse.ArgumentParser(description="Upload curriculum to Supabase")
    commands = sub.add_subparsers(dest="command", required=True)
    p_check = commands.add_parser("check", help="Run the upload-side gates without a database")
    p_check.add_argument("input", type=Path, help="output/<name>/ directory")
    p_check.add_argument("--item", help="Only this lesson id (e.g. g:architecture-decision-records)")
    for name, help_text in (("upload", "Upload a curriculum"), ("plan", "Dry run: print the plan, write nothing")):
        p = commands.add_parser(name, help=help_text)
        p.add_argument("input", type=Path, help="output/<name>/ directory")
        _add_target_flags(p)
        _add_upload_flags(p)
    p_enrich = commands.add_parser("enrich", help="Enrich an already-uploaded curriculum")
    p_enrich.add_argument("input", type=Path, help="output/<name>/ directory")
    _add_target_flags(p_enrich)
    return legacy, sub


def _parse_args(argv: list[str]) -> argparse.Namespace:
    legacy, sub = _build_parsers()
    if argv and argv[0] in SUBCOMMANDS:
        args = sub.parse_args(argv)
        if args.command == "plan":
            args.command, args.dry_run = "upload", True
        return args
    args = legacy.parse_args(argv)
    args.command = "enrich" if args.enrich else "upload"
    return args


def _run(args: argparse.Namespace, client_factory: Callable[[Target], Any]) -> int:
    input_dir = Path(args.input)
    if args.command == "check":
        if not input_dir.is_dir():
            raise UploadError(f"{input_dir} is not a directory", 2)
        findings = check_item(input_dir, args.item) if args.item else check(input_dir)
        errors, _ = print_findings(findings)
        return 1 if errors else 0

    if not input_dir.is_dir():
        raise UploadError(f"{input_dir} is not a directory", 2)
    if args.command == "upload":
        if args.owner == "org" and not args.org_id:
            raise UploadError("--org-id is required when --owner org", 2)
        if args.owner == "user" and bool(args.user_id) == bool(args.user_email):
            raise UploadError("--owner user needs exactly one of --user-id or --user-email", 2)
        if args.user_id and args.user_email:
            raise UploadError("--user-id and --user-email are mutually exclusive", 2)
        if args.replace_chunks and not args.update:
            raise UploadError("--replace-chunks requires --update", 2)

    target = resolve_target(args.target, args.db_url, args.db_key)
    console.print(f"Target: {target.host} ({target.name})")
    client = client_factory(target)

    if args.command == "enrich":
        enrich_curriculum(input_dir, client, target, skip_validation=args.skip_validation)
        return 0

    try:
        owner_id = resolve_owner_id(client, args.user_id, args.user_email)
    except TransientUploadError as e:
        raise _stopped(e, _RunState()) from e
    upload_curriculum(
        input_dir, client, target,
        owner_type=args.owner, user_id=owner_id, org_id=args.org_id,
        update_mode=args.update, replace_chunks=args.replace_chunks,
        skip_validation=args.skip_validation, dry_run=args.dry_run,
    )
    return 0


def main(argv: list[str] | None = None, client_factory: Callable[[Target], Any] = get_client) -> int:
    """CLI entry point. ``client_factory`` is injectable for tests."""
    args = _parse_args(sys.argv[1:] if argv is None else list(argv))
    try:
        return _run(args, client_factory)
    except UploadError as e:
        console.print(f"[red]Error:[/] {escape(str(e))}")
        return e.exit_code


if __name__ == "__main__":
    sys.exit(main())
