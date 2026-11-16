"""Tests for upload.py — driven by an in-memory FakeClient (no network, no .env).

The fake mimics the slice of supabase-py the uploader touches: chainable
PostgREST queries over per-table row lists, ``rpc()``, the admin user listing
and the Storage bucket API. Missing tables raise the real
``postgrest.exceptions.APIError`` with code PGRST205, exactly as PostgREST does.
"""

from __future__ import annotations

import json
import ssl
from pathlib import Path
from types import SimpleNamespace

import httpcore
import httpx
import pytest

import _compat  # noqa: F401

from postgrest.exceptions import APIError
from storage3.exceptions import StorageApiError

import upload
from upload import Target, UploadError

from conftest import read_json, write_json

USER = "11111111-1111-1111-1111-111111111111"
TARGET = Target(name="staging", url="https://staging.example.supabase.co", key="service-role-secret")


# ---------------------------------------------------------------------------
# FakeClient
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, data):
        self.data = data


def missing_table_error(name: str) -> APIError:
    return APIError({
        "message": f"Could not find the table 'public.{name}' in the schema cache",
        "code": "PGRST205", "hint": None, "details": None,
    })


class FakeQuery:
    """Chainable PostgREST-like query over one in-memory table."""

    def __init__(self, client: "FakeClient", name: str):
        self.client, self.name = client, name
        self.op, self.payload, self.filters, self._limit = "select", None, [], None
        self._order, self._range = None, None

    def select(self, _cols="*"):
        self.op = "select"
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def upsert(self, rows):
        self.op, self.payload = "upsert", rows
        return self

    def update(self, values):
        self.op, self.payload = "update", values
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        wanted = set(vals)
        self.filters.append(lambda r: r.get(col) in wanted)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r: (r.get(col) or 0) >= val)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def order(self, col):
        self._order = col
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        if self.name in self.client.missing_tables:
            raise missing_table_error(self.name)
        if self.name in self.client.table_errors:
            raise self.client.table_errors[self.name]
        self.client.attempts.append((self.name, self.op))
        fault = self.client.take_fault(self.name, self.op, self.payload)
        if fault and not fault.after_commit:
            raise fault.error  # the request never reached the database
        response = self._apply()
        if fault:
            raise fault.error  # committed, but the reply was lost
        return response

    def _apply(self):
        self.client.calls.append((self.name, self.op))
        rows = self.client.tables.setdefault(self.name, [])
        matching = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "select":
            data = matching
            if self._order:
                data = sorted(data, key=lambda r: str(r.get(self._order)))
            if self._range:
                # Postgres returns rows in no fixed order: a paged read without ORDER BY may
                # skip or repeat rows from one page to the next.
                assert self._order, "a ranged select must be ordered"
                data = data[self._range[0]: self._range[1] + 1]
            if self._limit is not None:
                data = data[: self._limit]
            if self.client.max_rows is not None:  # the server's silent cap on one response
                data = data[: self.client.max_rows]
            return FakeResponse([dict(r) for r in data])
        if self.op in ("insert", "upsert"):
            new = self.payload if isinstance(self.payload, list) else [self.payload]
            taken = {r.get("id") for r in rows}
            if any(r.get("id") in taken for r in new):  # primary key: the whole batch is refused
                raise APIError({"message": "duplicate key value violates unique constraint", "code": "23505",
                                "hint": None, "details": None})
            rows.extend(dict(r) for r in new)
            self.client.writes += len(new)
            return FakeResponse([dict(r) for r in new])
        if self.op == "update":
            for r in matching:
                r.update(self.payload)
            self.client.writes += len(matching)
            return FakeResponse([dict(r) for r in matching])
        for r in matching:  # delete
            rows.remove(r)
        self.client.writes += len(matching)
        return FakeResponse([dict(r) for r in matching])


class FakeRpc:
    def __init__(self, client: "FakeClient", name: str, params: dict):
        self.client, self.name, self.params = client, name, params

    def execute(self):
        self.client.rpc_calls.append((self.name, self.params))
        result = self.client.rpc_results.get(self.name)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


class FakeBucket:
    def __init__(self, client: "FakeClient", name: str):
        self.client, self.name = client, name

    def upload(self, path, data, opts):
        self.client.attempts.append(("storage", path))
        if path in self.client.failing_uploads:
            raise RuntimeError(f"upload failed for {path}")
        fault = self.client.take_fault("storage", "upload", path)
        if fault and not fault.after_commit:
            raise fault.error
        if any(path == stored for stored, _, _ in self.client.uploads):
            raise StorageApiError("The resource already exists", "Duplicate", 409)
        self.client.uploads.append((path, len(data), opts))
        self.client.writes += 1
        if fault:
            raise fault.error

    def get_public_url(self, path):
        return f"https://fake.test/storage/v1/object/public/{self.name}/{path}"


class FakeStorage:
    def __init__(self, client: "FakeClient"):
        self.client = client

    def get_bucket(self, name):
        return {"name": name}

    def create_bucket(self, name, options=None):
        self.client.writes += 1

    def from_(self, name):
        return FakeBucket(self.client, name)


class FakeAdmin:
    def __init__(self, client: "FakeClient"):
        self.client = client

    def list_users(self, page=None, per_page=None):
        self.client.list_users_calls.append((page, per_page))
        start = (page - 1) * per_page
        return self.client.users[start: start + per_page]


class Fault:
    """A failure injected into the fake: ``times`` matching requests raise ``error``, either
    before the request takes effect or (``after_commit``) after it did, like a lost reply.
    ``when(payload)`` narrows which requests match; ``skip`` lets the first ones through."""

    def __init__(self, table, op, error, times=1, after_commit=False, when=None, skip=0):
        self.table, self.op, self.error, self.times = table, op, error, times
        self.after_commit, self.when, self.skip = after_commit, when, skip


class FakeClient:
    def __init__(self, missing_tables: set[str] | None = None):
        self.tables: dict[str, list[dict]] = {}
        self.missing_tables = set(missing_tables or ())
        self.table_errors: dict[str, Exception] = {}
        self.faults: list[Fault] = []
        self.attempts: list[tuple[str, str]] = []  # every request, failed ones included
        self.max_rows: int | None = None
        self.calls: list[tuple[str, str]] = []
        self.writes = 0
        self.rpc_results: dict[str, object] = {}
        self.rpc_calls: list[tuple[str, dict]] = []
        self.users: list[SimpleNamespace] = []
        self.list_users_calls: list[tuple[int, int]] = []
        self.uploads: list[tuple[str, int, dict]] = []
        self.failing_uploads: set[str] = set()
        self.storage = FakeStorage(self)
        self.auth = SimpleNamespace(admin=FakeAdmin(self))

    def table(self, name):
        return FakeQuery(self, name)

    def rpc(self, name, params):
        return FakeRpc(self, name, params)

    def fail(self, table, op, error, **kwargs) -> Fault:
        fault = Fault(table, op, error, **kwargs)
        self.faults.append(fault)
        return fault

    def take_fault(self, table, op, payload) -> Fault | None:
        for fault in self.faults:
            if (fault.table, fault.op) != (table, op) or fault.times <= 0:
                continue
            if fault.when is not None and not fault.when(payload):
                continue
            if fault.skip > 0:
                fault.skip -= 1
                continue
            fault.times -= 1
            return fault
        return None

    def rows(self, name) -> list[dict]:
        return self.tables.get(name, [])

    def topic_id(self, title) -> str:
        return next(r["id"] for r in self.rows("topics") if r["title"] == title)

    def chunk_ids(self) -> dict[tuple[str, int], str]:
        return {(r["topic_id"], r["chunk_index"]): r["id"] for r in self.rows("content_chunks")}


# ---------------------------------------------------------------------------
# Lesson-mode fixture
# ---------------------------------------------------------------------------

ADR = "Architecture Decision Records"
WAD = "What Architects Do"


def page(title: str, index: int, text: str, images: list[dict] | None = None) -> dict:
    return {
        "topic_path": ["Architecture Leadership", "Recording Decisions", title],
        "chunk_index": index,
        "content": text,
        "token_count": 120,
        "has_code": False,
        "page_number": None,
        "images": images or [],
        "metadata": {"lesson_id": "g:adr" if title == ADR else "g:what-architects-do",
                     "page_role": "core", "contract_version": 1},
    }


def build_lesson_dir(root: Path) -> Path:
    cur = root / "adr"
    (cur / "extracted" / "images").mkdir(parents=True)
    for name in ("img_adr_1.png", "img_adr_2.png"):
        (cur / "extracted" / "images" / name).write_bytes(b"\x89PNG fake")

    write_json(cur / "manifest.json", {
        "name": "ADR Curriculum", "domain": "adr-cur", "domain_family": "adr-cur",
        "variant": "extensive", "description": "Lesson-mode fixture", "pipeline": "lesson",
        "sources": [{"type": "pdf", "path": "/src/adr.pdf", "title": "ADR Book"}],
    })
    write_json(cur / "structure.json", [{
        "title": "Architecture Leadership", "depth": 0, "sort_order": 0, "suggested_level": 2,
        "description": "Area", "learning_objectives": [{"text": "Summarize leadership", "bloom_level": "understand"}],
        "children": [{
            "title": "Recording Decisions", "depth": 1, "sort_order": 0, "description": "Module",
            "children": [
                {"title": WAD, "depth": 2, "sort_order": 0, "lesson_id": "g:what-architects-do",
                 "description": "One line.", "source_sections": [],
                 "learning_objectives": [{"text": "Describe the architect's role", "bloom_level": "understand"}],
                 "children": []},
                {"title": ADR, "depth": 2, "sort_order": 1, "lesson_id": "g:adr",
                 "description": "One line.", "source_sections": [],
                 "learning_objectives": [{"text": "Write an ADR", "bloom_level": "apply"}],
                 "prerequisites": [{"topic": WAD, "strength": "recommended"}],
                 "children": []},
            ],
        }],
    }])
    write_json(cur / "chunks.json", [
        page(WAD, 0, "## Architects\n\nArchitects decide."),
        page(ADR, 0, "## Why decisions get lost\n\nTeams forget."),
        page(ADR, 1, "## The template\n\n![ADR template](image:img_adr_1)\n\nContext, decision, consequences.",
             images=[{"id": "img_adr_1", "local_path": "images/img_adr_1.png",
                      "alt_text": "The ADR template", "mime_type": "image/png"}]),
        page(ADR, 2, "## Lifecycle\n\nProposed, accepted, superseded.",
             images=[{"id": "img_adr_2", "local_path": "images/img_adr_2.png",
                      "alt_text": "Lifecycle", "mime_type": "image/png"}]),
    ])
    (cur / "lessons").mkdir()
    write_json(cur / "lessons" / "g_adr.json", {
        "contract_version": 1, "lesson_id": "g:adr", "group_id": "g:adr", "title": ADR, "pages": [],
    })
    (cur / "questions").mkdir()
    write_json(cur / "questions" / "g_adr.json", {
        "contract_version": 1, "lesson_id": "g:adr", "revision": 0,
        "embedded": [{
            "id": "g:adr#q1", "kind": "check", "page_index": 1, "position": "after",
            "lo_id": "g:adr#lo1", "concept_id": "c:adr", "unit_ids": ["u:adr:0:1"], "source_quote": "q",
            "prompt": "What happens to an old ADR when the decision changes?",
            "acceptance": {"prediction": "marked superseded", "causal_reason": "history is preserved"},
            "expected_answer": "It is marked superseded.", "rationale": "r", "key_points": ["superseded"],
            "bloom_level": "understand",
        }],
        "quiz": [
            {"id": "g:adr#q7", "type": "multiple_choice", "lo_id": "g:adr#lo1", "concept_id": "c:adr",
             "unit_ids": ["u:adr:0:1"], "page_index": 2, "source_quote": "q",
             "prompt": "Which section of an ADR records the trade-offs?",
             "options": ["Context", "Status", "Consequences", "Title"], "correct_index": 2,
             "distractors": [{"index": 0, "misconception": "context is not outcome"}],
             "acceptance": {"prediction": "consequences", "causal_reason": "outcomes live there"},
             "expected_answer": "Consequences", "rationale": "r", "key_points": ["consequences"],
             "explanation": "Consequences hold the trade-offs.", "bloom_level": "apply", "mastery_level": 3,
             "feedback": {"confirmatory": "yes", "remedial_hint": "look at outcomes"},
             "variants": [{"id": "g:adr#q7v1", "prompt": "Where are trade-offs?", "options": ["a", "b", "c", "d"], "correct_index": 1}],
             "recap_of": None},
            {"id": "g:adr#q8", "type": "short_answer", "lo_id": "g:adr#lo1", "concept_id": "c:adr",
             "unit_ids": ["u:adr:0:1"], "page_index": None, "source_quote": "q",
             "prompt": "Judge whether deleting superseded ADRs is a good practice and why.",
             "acceptance": {"prediction": "no", "causal_reason": "reasoning history is lost"},
             "expected_answer": "No", "rationale": "r", "key_points": ["history"],
             "explanation": "History matters.", "bloom_level": "evaluate", "recap_of": None},
        ],
        "exercises": [{
            "id": "g:adr#e1", "title": "Write an ADR", "problem_statement": "Record the decision to adopt CQRS.",
            "hints": ["Start with context", "State the decision", "List consequences"],
            "expected_solution": "An ADR with three sections.", "common_mistakes": ["Skipping consequences"],
            "bloom_level": "apply", "difficulty": 2, "fading": "completion", "lo_id": "g:adr#lo1", "unit_ids": ["u:adr:0:1"],
        }],
        "lessons_learned": [],
    })
    (cur / "cards").mkdir()
    write_json(cur / "cards" / "g_adr.json", {
        "contract_version": 1, "lesson_id": "g:adr", "revision": 0,
        "cards": [
            {"id": "g:adr#k1", "card_type": "concept", "front": "What three sections does every ADR record?",
             "back": "Context, decision, consequences.", "card_data": {}, "lo_id": "g:adr#lo1",
             "unit_ids": ["u:adr:0:1"], "page_index": 1, "source_reference": "adr.github.io", "lens": "parts_wholes"},
            {"id": "g:adr#k2", "card_type": "cloze", "front": "A replaced ADR is marked ___ rather than deleted.",
             "back": "superseded", "card_data": {"blank_answer": "superseded", "hint": "status word"},
             "lo_id": "g:adr#lo1", "unit_ids": ["u:adr:0:1"], "page_index": 0, "source_reference": "adr.github.io", "lens": "recall"},
        ],
        "lessons_learned": [],
    })
    write_json(cur / "review.json", {"approved": True, "validation": {"errors": 0, "warnings": 0}})
    return cur


@pytest.fixture
def lesson_dir(tmp_path: Path) -> Path:
    return build_lesson_dir(tmp_path)


def run_upload(input_dir: Path, client: FakeClient, **kwargs) -> dict:
    kwargs.setdefault("skip_validation", True)
    return upload.upload_curriculum(input_dir, client, TARGET, owner_type="user", user_id=USER, **kwargs)


def error_ids(findings) -> list[str]:
    return sorted(f.check_id for f in findings if f.severity == "error")


@pytest.fixture
def no_dotenv(monkeypatch):
    """Keep the real .env out of target resolution and clear every variable it reads."""
    monkeypatch.setattr(upload, "load_dotenv", lambda *a, **k: False)
    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "STAGING_SUPABASE_URL",
                "STAGING_SUPABASE_SERVICE_ROLE_KEY", "CUSTOM_DB_URL", "CUSTOM_DB_SERVICE_KEY"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


def test_resolve_target_staging_from_env():
    env = {"STAGING_SUPABASE_URL": "https://stg.supabase.co", "STAGING_SUPABASE_SERVICE_ROLE_KEY": "sk-staging",
           "SUPABASE_URL": "https://prod.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "sk-prod"}
    target = upload.resolve_target("staging", env=env)
    assert (target.name, target.host, target.url) == ("staging", "stg.supabase.co", "https://stg.supabase.co")
    assert "sk-staging" not in repr(target)


def test_resolve_target_staging_missing_vars_is_exit_2():
    with pytest.raises(UploadError) as ei:
        upload.resolve_target("staging", env={"SUPABASE_URL": "https://prod.supabase.co"})
    assert ei.value.exit_code == 2
    assert "STAGING_SUPABASE_URL" in str(ei.value)


def test_resolve_target_refuses_staging_equal_to_prod():
    env = {"STAGING_SUPABASE_URL": "https://prod.supabase.co/", "STAGING_SUPABASE_SERVICE_ROLE_KEY": "sk",
           "SUPABASE_URL": "https://PROD.supabase.co"}
    with pytest.raises(UploadError) as ei:
        upload.resolve_target("staging", env=env)
    assert ei.value.exit_code == 2
    assert "Refusing" in str(ei.value)


def test_resolve_target_custom_from_flags_and_default_from_env():
    custom = upload.resolve_target("custom", db_url="https://db.local", db_key="k", env={})
    assert (custom.name, custom.host) == ("custom", "db.local")
    default = upload.resolve_target("default", env={"SUPABASE_URL": "https://prod.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"})
    assert default.name == "default"


def test_main_requires_target_flag(lesson_dir: Path):
    with pytest.raises(SystemExit) as ei:
        upload.main(["--input", str(lesson_dir), "--owner", "user", "--user-id", USER])
    assert ei.value.code == 2


def test_main_staging_missing_env_exits_2_without_client(lesson_dir: Path, no_dotenv):
    created = []
    rc = upload.main(["--input", str(lesson_dir), "--target", "staging", "--owner", "user", "--user-id", USER],
                     client_factory=lambda t: created.append(t))
    assert rc == 2 and created == []


def test_main_staging_equal_to_prod_exits_2(lesson_dir: Path, no_dotenv):
    no_dotenv.setenv("SUPABASE_URL", "https://prod.supabase.co")
    no_dotenv.setenv("STAGING_SUPABASE_URL", "https://prod.supabase.co")
    no_dotenv.setenv("STAGING_SUPABASE_SERVICE_ROLE_KEY", "sk")
    created = []
    rc = upload.main(["--input", str(lesson_dir), "--target", "staging", "--owner", "user", "--user-id", USER],
                     client_factory=lambda t: created.append(t))
    assert rc == 2 and created == []


# ---------------------------------------------------------------------------
# Owner resolution
# ---------------------------------------------------------------------------


def test_resolve_owner_via_rpc():
    client = FakeClient()
    client.rpc_results["find_user_by_email"] = USER
    assert upload.resolve_owner_id(client, user_email="Me@Example.com") == USER
    assert client.rpc_calls == [("find_user_by_email", {"p_email": "Me@Example.com"})]
    assert client.list_users_calls == []


def test_resolve_owner_admin_fallback_paginates_case_insensitively():
    client = FakeClient()
    client.rpc_results["find_user_by_email"] = APIError({"message": "function not found", "code": "PGRST202"})
    client.users = [SimpleNamespace(id=f"u{i}", email=f"user{i}@example.com") for i in range(1000)]
    client.users.append(SimpleNamespace(id=USER, email="ME@Example.com"))
    assert upload.resolve_owner_id(client, user_email="me@example.com") == USER
    assert client.list_users_calls == [(1, 1000), (2, 1000)]


def test_resolve_owner_rpc_empty_result_falls_back():
    client = FakeClient()
    client.rpc_results["find_user_by_email"] = None
    client.users = [SimpleNamespace(id=USER, email="me@example.com")]
    assert upload.resolve_owner_id(client, user_email="me@example.com") == USER
    assert client.list_users_calls == [(1, 1000)]


def test_resolve_owner_not_found_is_exit_2():
    client = FakeClient()
    client.rpc_results["find_user_by_email"] = None
    with pytest.raises(UploadError) as ei:
        upload.resolve_owner_id(client, user_email="ghost@example.com")
    assert ei.value.exit_code == 2


def test_resolve_owner_user_id_passthrough_and_exclusivity():
    client = FakeClient()
    assert upload.resolve_owner_id(client, user_id=USER) == USER
    assert upload.resolve_owner_id(client) is None
    with pytest.raises(UploadError) as ei:
        upload.resolve_owner_id(client, user_id=USER, user_email="me@example.com")
    assert ei.value.exit_code == 2


# ---------------------------------------------------------------------------
# has_table
# ---------------------------------------------------------------------------


def test_has_table_true_and_false():
    client = FakeClient(missing_tables={"topic_cards"})
    assert upload.has_table(client, "topics") is True
    assert upload.has_table(client, "topic_cards") is False


@pytest.mark.parametrize("error", [
    {"message": "x", "code": "42P01"},
    {"message": 'relation "public.topic_cards" does not exist', "code": None},
    {"message": "Could not find the table in the schema cache", "code": "PGRST999"},
])
def test_has_table_false_on_missing_table_signatures(error):
    client = FakeClient()
    client.table_errors["topic_cards"] = APIError({"hint": None, "details": None, **error})
    assert upload.has_table(client, "topic_cards") is False


def test_has_table_reraises_other_api_errors():
    client = FakeClient()
    client.table_errors["topics"] = APIError({"message": "permission denied for table topics", "code": "42501"})
    with pytest.raises(APIError):
        upload.has_table(client, "topics")


def test_probe_practice_tables_reports_missing():
    client = FakeClient(missing_tables={"topic_exercises", "topic_cards"})
    tables = upload.probe_practice_tables(client)
    assert tables == {"reading_questions": True, "topic_questions": True, "topic_cards": False, "topic_exercises": False}


# ---------------------------------------------------------------------------
# insert_chunks
# ---------------------------------------------------------------------------


def test_insert_chunks_returns_id_map_and_stays_tuple_compatible():
    client = FakeClient()
    chunks = [
        {"topic_path": ["Area", "Lesson A"], "chunk_index": 0, "content": "first", "has_code": False,
         "metadata": {"lesson_id": "g:a", "page_role": "orientation"}},
        {"topic_path": ["Area", "Lesson A"], "chunk_index": 1, "content": "second", "has_code": True},
        {"topic_path": ["Area", "Ghost"], "chunk_index": 0, "content": "unmatched"},
    ]
    result = upload.insert_chunks(client, chunks, {"Lesson A": "t1"})
    inserted, skipped, replaced = result
    assert (inserted, skipped, replaced) == (2, 1, 0)
    assert set(result.chunk_ids) == {("t1", 0), ("t1", 1)}
    assert result.chunk_ids == client.chunk_ids()
    meta = json.loads(client.rows("content_chunks")[0]["metadata"])
    assert meta == {"has_code": False, "lesson_id": "g:a", "page_role": "orientation"}


def test_insert_chunks_update_mode_keeps_existing_ids_in_map():
    client = FakeClient()
    client.tables["content_chunks"] = [{"id": "c-old", "topic_id": "t1", "chunk_index": 0, "content": "old"}]
    chunks = [{"topic_path": ["Lesson A"], "chunk_index": i, "content": f"page {i}"} for i in range(2)]
    result = upload.insert_chunks(client, chunks, {"Lesson A": "t1"}, update_mode=True)
    assert (result.inserted, result.skipped, result.replaced) == (1, 1, 0)
    assert result.chunk_ids[("t1", 0)] == "c-old"
    assert result.chunk_ids[("t1", 1)] != "c-old"


def test_insert_chunks_replace_mode_replaces_and_maps_new_ids():
    client = FakeClient()
    client.tables["content_chunks"] = [{"id": "c-old", "topic_id": "t1", "chunk_index": 0, "content": "old"}]
    chunks = [{"topic_path": ["Lesson A"], "chunk_index": 0, "content": "new"}]
    result = upload.insert_chunks(client, chunks, {"Lesson A": "t1"}, update_mode=True, replace_chunks=True)
    assert (result.inserted, result.skipped, result.replaced) == (1, 0, 1)
    assert result.chunk_ids[("t1", 0)] != "c-old"
    assert [r["content"] for r in client.rows("content_chunks")] == ["new"]


# ---------------------------------------------------------------------------
# Lesson-mode upload
# ---------------------------------------------------------------------------


def test_lesson_mode_inserts_all_four_tables(lesson_dir: Path):
    client = FakeClient()
    result = run_upload(lesson_dir, client)
    adr = client.topic_id(ADR)
    chunk_ids = client.chunk_ids()

    (rq,) = client.rows("reading_questions")
    assert rq["topic_id"] == adr and rq["chunk_id"] == chunk_ids[(adr, 1)]
    assert (rq["question_type"], rq["position"], rq["sort_order"]) == ("check", "after", 1)
    assert rq["question_text"].startswith("What happens") and rq["expected_answer"] == "It is marked superseded."

    mcq, short = client.rows("topic_questions")
    assert (mcq["question_type"], mcq["bloom_level"], mcq["mastery_level"], mcq["sort_order"]) == ("multiple_choice", "apply", 3, 0)
    assert mcq["chunk_id"] == chunk_ids[(adr, 2)] and mcq["explanation"] == "Consequences hold the trade-offs."
    assert set(mcq["payload"]) == set(upload.TOPIC_QUESTION_PAYLOAD_KEYS)
    assert mcq["payload"]["options"] == ["Context", "Status", "Consequences", "Title"]
    assert mcq["payload"]["correct_index"] == 2 and mcq["payload"]["lo_id"] == "g:adr#lo1"
    assert mcq["payload"]["variants"][0]["id"] == "g:adr#q7v1"
    assert (short["question_type"], short["mastery_level"], short["chunk_id"], short["sort_order"]) == ("short_answer", 4, None, 1)
    assert short["payload"]["options"] is None

    concept, cloze = client.rows("topic_cards")
    assert (concept["card_type"], concept["sort_order"], concept["chunk_id"]) == ("concept", 0, chunk_ids[(adr, 1)])
    assert concept["source_reference"] == "adr.github.io" and concept["card_data"] == {}
    assert (cloze["card_type"], cloze["sort_order"], cloze["chunk_id"]) == ("cloze", 1, chunk_ids[(adr, 0)])
    assert cloze["card_data"]["blank_answer"] == "superseded"

    (ex,) = client.rows("topic_exercises")
    assert (ex["topic_id"], ex["title"], ex["bloom_level"], ex["difficulty"], ex["sort_order"]) == (adr, "Write an ADR", "apply", 2, 0)
    assert ex["hints"] == ["Start with context", "State the decision", "List consequences"]
    assert ex["common_mistakes"] == ["Skipping consequences"]

    assert not [r for r in client.rows("content_chunks") if r["chunk_index"] >= upload.EXERCISE_CHUNK_OFFSET]
    assert {k: result[k] for k in ("reading_questions", "topic_questions", "topic_cards", "topic_exercises", "exercise_chunks")} == {
        "reading_questions": 1, "topic_questions": 2, "topic_cards": 2, "topic_exercises": 1, "exercise_chunks": 0,
    }
    assert result["skipped_tables"] == []
    assert len(client.rows("topic_prerequisites")) == 1 and len(client.rows("topic_learning_objectives")) == 3


def test_fallback_to_exercise_chunks_when_topic_exercises_missing(lesson_dir: Path):
    client = FakeClient(missing_tables={"topic_exercises"})
    result = run_upload(lesson_dir, client)
    (ex_chunk,) = [r for r in client.rows("content_chunks") if r["chunk_index"] >= upload.EXERCISE_CHUNK_OFFSET]
    assert ex_chunk["topic_id"] == client.topic_id(ADR) and ex_chunk["content"] == "Record the decision to adopt CQRS."
    meta = json.loads(ex_chunk["metadata"])
    assert meta["type"] == "exercise" and meta["exercise"]["title"] == "Write an ADR"
    assert result["skipped_tables"] == ["topic_exercises"]
    assert (result["topic_exercises"], result["exercise_chunks"]) == (0, 1)
    assert (result["reading_questions"], result["topic_questions"], result["topic_cards"]) == (1, 2, 2)
    assert "topic_exercises" not in {name for name, op in client.calls if op == "insert"}


def test_all_practice_tables_missing_only_skips_them(lesson_dir: Path):
    client = FakeClient(missing_tables=set(upload.PRACTICE_TABLES))
    result = run_upload(lesson_dir, client)
    assert result["skipped_tables"] == list(upload.PRACTICE_TABLES)
    assert result["exercise_chunks"] == 1 and result["topic_questions"] == 0
    assert result["chunks_inserted"] == 4


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def test_placeholder_substitution_and_legacy_append(lesson_dir: Path):
    client = FakeClient()
    run_upload(lesson_dir, client)
    adr = client.topic_id(ADR)
    by_index = {r["chunk_index"]: r for r in client.rows("content_chunks") if r["topic_id"] == adr}
    url1 = "https://fake.test/storage/v1/object/public/curriculum-images/adr-cur/img_adr_1.png"
    url2 = "https://fake.test/storage/v1/object/public/curriculum-images/adr-cur/img_adr_2.png"
    assert f"![ADR template]({url1})" in by_index[1]["content"]
    assert "image:" not in by_index[1]["content"]
    assert by_index[1]["content"].count(url1) == 1  # substituted, not also appended
    assert by_index[2]["content"].rstrip().endswith(f"![Lifecycle]({url2})")  # legacy append
    assert "![" not in by_index[0]["content"]
    assert json.loads(by_index[1]["metadata"])["images"] == [{"id": "img_adr_1", "alt_text": "The ADR template", "url": url1}]
    assert sorted(path for path, _, _ in client.uploads) == ["adr-cur/img_adr_1.png", "adr-cur/img_adr_2.png"]


def test_inline_chunk_images_reports_unresolved_and_keeps_placeholder():
    chunks = [{"topic_path": ["Lesson"], "chunk_index": 1, "content": "x ![a](image:ghost) y",
               "images": [{"id": "other", "url": "https://u/other.png"}]}]
    assert upload.inline_chunk_images(chunks) == ["Lesson #1: ghost"]
    assert chunks[0]["content"] == "x ![a](image:ghost) y"


def test_upload_fails_with_exit_1_when_placeholder_image_upload_fails(lesson_dir: Path):
    client = FakeClient()
    client.failing_uploads.add("adr-cur/img_adr_1.png")
    with pytest.raises(UploadError) as ei:
        run_upload(lesson_dir, client)
    assert ei.value.exit_code == 1 and "img_adr_1" in str(ei.value)
    assert client.rows("content_chunks") == []
    assert not (lesson_dir / "upload_result.json").exists()


def test_unknown_placeholder_id_is_caught_before_any_write(lesson_dir: Path):
    chunks = read_json(lesson_dir / "chunks.json")
    chunks[2]["content"] = chunks[2]["content"].replace("image:img_adr_1", "image:ghost")
    write_json(lesson_dir / "chunks.json", chunks)
    assert "upload.image_placeholder_unresolved" in error_ids(upload.check(lesson_dir))
    client = FakeClient()
    with pytest.raises(UploadError) as ei:
        run_upload(lesson_dir, client)
    assert ei.value.exit_code == 1 and client.writes == 0


def test_missing_image_file_is_error_only_when_referenced_by_placeholder(lesson_dir: Path):
    (lesson_dir / "extracted" / "images" / "img_adr_2.png").unlink()  # legacy-append image
    findings = upload.check(lesson_dir)
    assert [f.severity for f in findings if f.check_id == "upload.image_file_missing"] == ["warning"]
    (lesson_dir / "extracted" / "images" / "img_adr_1.png").unlink()  # placeholder image
    assert "upload.image_file_missing" in error_ids(upload.check(lesson_dir))


# ---------------------------------------------------------------------------
# upload_result.json / dry run
# ---------------------------------------------------------------------------


def test_upload_result_json_has_the_contract_keys(lesson_dir: Path):
    client = FakeClient()
    result = run_upload(lesson_dir, client)
    written = read_json(lesson_dir / "upload_result.json")
    assert written == result
    assert list(written) == [
        "domain_id", "target", "owner_type", "owner_id", "topics_inserted", "topics_skipped", "chunks_inserted",
        "chunks_skipped", "chunks_replaced", "reading_questions", "topic_questions", "topic_cards",
        "topic_exercises", "exercise_chunks", "skipped_tables", "update_mode", "uploaded_at", "contract_version",
    ]
    assert written["domain_id"] == client.rows("domains")[0]["id"]
    assert (written["target"], written["owner_type"], written["owner_id"]) == ("staging", "user", USER)
    assert (written["topics_inserted"], written["topics_skipped"]) == (4, 0)
    assert (written["chunks_inserted"], written["chunks_skipped"], written["chunks_replaced"]) == (4, 0, 0)
    assert written["update_mode"] == "insert" and written["contract_version"] == 1
    assert written["uploaded_at"].endswith("+00:00")


def test_dry_run_writes_nothing(lesson_dir: Path, capsys):
    client = FakeClient(missing_tables={"topic_exercises"})
    result = run_upload(lesson_dir, client, dry_run=True)
    assert client.writes == 0 and client.uploads == []
    assert all(op == "select" for _, op in client.calls)
    assert not (lesson_dir / "upload_result.json").exists()
    assert result["domain_id"] is None
    assert (result["topic_questions"], result["topic_cards"], result["reading_questions"]) == (2, 2, 1)
    assert (result["topic_exercises"], result["exercise_chunks"], result["skipped_tables"]) == (0, 1, ["topic_exercises"])
    out = capsys.readouterr().out
    assert "nothing will be written" in out and "staging.example.supabase.co" in out and USER in out


# ---------------------------------------------------------------------------
# check() gates
# ---------------------------------------------------------------------------


def test_check_clean_lesson_dir_only_warns_about_lesson_without_questions(lesson_dir: Path):
    findings = upload.check(lesson_dir)
    assert error_ids(findings) == []
    assert [(f.check_id, f.severity) for f in findings] == [("upload.lesson_without_questions", "warning")]
    assert WAD in findings[0].location


def test_check_raw_fixture_is_clean(curriculum_dir: Path):
    assert upload.check(curriculum_dir) == []


def test_check_missing_files(tmp_path: Path):
    findings = upload.check(tmp_path)
    assert error_ids(findings) == ["upload.file_missing"] * 3


def test_check_detects_unresolved_lesson(lesson_dir: Path):
    path = lesson_dir / "questions" / "g_adr.json"
    data = read_json(path)
    data["lesson_id"] = "g:ghost"
    write_json(path, data)
    findings = upload.check(lesson_dir)
    assert "upload.lesson_unresolved" in error_ids(findings)
    assert any(f.location == "g:ghost" for f in findings)


def test_check_falls_back_to_lesson_title_when_leaf_has_no_lesson_id(lesson_dir: Path):
    structure = read_json(lesson_dir / "structure.json")
    del structure[0]["children"][0]["children"][1]["lesson_id"]
    write_json(lesson_dir / "structure.json", structure)
    assert error_ids(upload.check(lesson_dir)) == []
    client = FakeClient()
    run_upload(lesson_dir, client)
    assert len(client.rows("topic_questions")) == 2


def test_check_detects_unresolved_page_index_and_bad_enums(lesson_dir: Path):
    path = lesson_dir / "questions" / "g_adr.json"
    data = read_json(path)
    data["embedded"][0]["page_index"] = 9
    data["quiz"][0]["type"] = "essay"
    data["quiz"][1]["bloom_level"] = "guess"
    write_json(path, data)
    cards_path = lesson_dir / "cards" / "g_adr.json"
    cards = read_json(cards_path)
    cards["cards"][0]["card_type"] = "riddle"
    cards["cards"][1]["front"] = ""
    write_json(cards_path, cards)
    findings = upload.check(lesson_dir)
    assert error_ids(findings) == ["upload.enum_invalid", "upload.enum_invalid", "upload.field_missing", "upload.page_index_unresolved"]
    assert any(f.check_id == "upload.enum_invalid" and f.severity == "warning" and "bloom" in f.message for f in findings)


def test_check_item_scopes_to_one_lesson(lesson_dir: Path):
    assert upload.check_item(lesson_dir, "g:adr") == []
    assert error_ids(upload.check_item(lesson_dir, "g:nope")) == ["upload.item_missing"]


def test_check_duplicate_title_is_error_in_lesson_mode_warning_in_raw(lesson_dir: Path, curriculum_dir: Path):
    structure = read_json(lesson_dir / "structure.json")
    structure[0]["children"][0]["title"] = ADR
    write_json(lesson_dir / "structure.json", structure)
    assert "upload.duplicate_title" in error_ids(upload.check(lesson_dir))

    raw = read_json(curriculum_dir / "structure.json")
    raw[1]["title"] = raw[0]["title"]
    write_json(curriculum_dir / "structure.json", raw)
    assert [(f.check_id, f.severity) for f in upload.check(curriculum_dir)] == [("upload.duplicate_title", "warning")]


def test_check_invalid_practice_json_is_a_finding_not_a_traceback(lesson_dir: Path):
    (lesson_dir / "cards" / "g_adr.json").write_text("{not json", encoding="utf-8")
    assert "upload.invalid_json" in error_ids(upload.check(lesson_dir))


# ---------------------------------------------------------------------------
# Update / replace / enrich
# ---------------------------------------------------------------------------


def test_update_mode_clears_and_reinserts_practice_rows(lesson_dir: Path):
    client = FakeClient()
    run_upload(lesson_dir, client)
    first_question_ids = {r["id"] for r in client.rows("topic_questions")}
    old_chunk_ids = client.chunk_ids()

    result = run_upload(lesson_dir, client, update_mode=True)
    assert (result["topics_inserted"], result["topics_skipped"]) == (0, 4)
    assert (result["chunks_inserted"], result["chunks_skipped"], result["chunks_replaced"]) == (0, 4, 0)
    assert result["update_mode"] == "update"
    assert len(client.rows("topic_questions")) == 2
    assert {r["id"] for r in client.rows("topic_questions")}.isdisjoint(first_question_ids)
    assert len(client.rows("topic_cards")) == 2 and len(client.rows("topic_exercises")) == 1
    (rq,) = client.rows("reading_questions")
    assert rq["chunk_id"] == old_chunk_ids[(client.topic_id(ADR), 1)]  # relinked to the kept chunk


def test_replace_chunks_relinks_practice_rows_to_new_chunks(lesson_dir: Path):
    client = FakeClient()
    run_upload(lesson_dir, client)
    old_chunk_ids = set(client.chunk_ids().values())
    result = run_upload(lesson_dir, client, update_mode=True, replace_chunks=True)
    assert (result["chunks_inserted"], result["chunks_replaced"], result["update_mode"]) == (4, 4, "replace_chunks")
    new_chunk_ids = client.chunk_ids()
    assert old_chunk_ids.isdisjoint(new_chunk_ids.values())
    (rq,) = client.rows("reading_questions")
    assert rq["chunk_id"] == new_chunk_ids[(client.topic_id(ADR), 1)]


def test_update_mode_with_missing_exercise_table_resets_exercise_chunks(lesson_dir: Path):
    client = FakeClient(missing_tables={"topic_exercises"})
    run_upload(lesson_dir, client)
    run_upload(lesson_dir, client, update_mode=True)
    assert len([r for r in client.rows("content_chunks") if r["chunk_index"] >= upload.EXERCISE_CHUNK_OFFSET]) == 1


def test_enrich_clears_and_reinserts_practice_content(lesson_dir: Path):
    client = FakeClient()
    uploaded = run_upload(lesson_dir, client)
    stale = {"id": "stale", "topic_id": client.topic_id(ADR), "question_type": "check", "sort_order": 0}
    client.tables["reading_questions"].append(stale)

    result = upload.enrich_curriculum(lesson_dir, client, TARGET, skip_validation=True)
    assert result["domain_id"] == uploaded["domain_id"] and result["update_mode"] == "enrich"
    assert (result["reading_questions"], result["topic_questions"], result["topic_cards"], result["topic_exercises"]) == (1, 2, 2, 1)
    assert "stale" not in {r["id"] for r in client.rows("reading_questions")}
    assert len(client.rows("topic_learning_objectives")) == 3 and len(client.rows("topic_prerequisites")) == 1
    assert len(client.rows("topic_questions")) == 2
    (rq,) = client.rows("reading_questions")
    assert rq["chunk_id"] == client.chunk_ids()[(client.topic_id(ADR), 1)]
    assert read_json(lesson_dir / "upload_result.json")["domain_id"] == uploaded["domain_id"]


# ---------------------------------------------------------------------------
# Raw mode compatibility + validator pre-flight
# ---------------------------------------------------------------------------


def test_raw_mode_upload_runs_the_real_validator_and_exercise_chunks(curriculum_dir: Path):
    client = FakeClient()
    result = upload.upload_curriculum(curriculum_dir, client, TARGET, owner_type="user", user_id=USER)
    assert (result["topics_inserted"], result["chunks_inserted"], result["exercise_chunks"]) == (15, 10, 5)
    assert result["skipped_tables"] == [] and result["topic_questions"] == 0
    assert not {name for name, _ in client.calls} & set(upload.PRACTICE_TABLES)
    assert len(client.rows("books")) == 2 and len(client.rows("curriculum_levels")) == 3
    assert len(client.rows("topic_prerequisites")) == 1
    domain = client.rows("domains")[0]
    assert (domain["slug"], domain["domain_family"], domain["variant"], domain["owner_id"]) == ("mini", "mini", "extensive", USER)


def test_validator_preflight_blocks_with_exit_1(lesson_dir: Path, monkeypatch):
    import validate

    monkeypatch.setattr(validate, "run_validation", lambda d, s: [validate.Finding("structure.empty_leaf", "error", "x", "boom")])
    client = FakeClient()
    with pytest.raises(UploadError) as ei:
        upload.upload_curriculum(lesson_dir, client, TARGET, owner_type="user", user_id=USER)
    assert ei.value.exit_code == 1 and client.writes == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_main_upload_with_user_email_prints_target_and_writes_result(lesson_dir: Path, no_dotenv, capsys):
    no_dotenv.setenv("STAGING_SUPABASE_URL", "https://stg.supabase.co")
    no_dotenv.setenv("STAGING_SUPABASE_SERVICE_ROLE_KEY", "sk-staging")
    client = FakeClient()
    client.rpc_results["find_user_by_email"] = USER
    seen: list[Target] = []

    def factory(target: Target):
        seen.append(target)
        return client

    rc = upload.main(["--input", str(lesson_dir), "--target", "staging", "--owner", "user",
                      "--user-email", "me@example.com", "--skip-validation"], client_factory=factory)
    assert rc == 0 and seen[0].name == "staging"
    out = capsys.readouterr().out
    assert "Target: stg.supabase.co (staging)" in out and "sk-staging" not in out
    assert read_json(lesson_dir / "upload_result.json")["owner_id"] == USER


def test_main_owner_user_needs_exactly_one_identifier(lesson_dir: Path, no_dotenv):
    base = ["--input", str(lesson_dir), "--target", "staging", "--owner", "user"]
    assert upload.main(base, client_factory=lambda t: FakeClient()) == 2
    assert upload.main(base + ["--user-id", USER, "--user-email", "x@y.z"], client_factory=lambda t: FakeClient()) == 2


def test_main_replace_chunks_requires_update(lesson_dir: Path, no_dotenv):
    rc = upload.main(["--input", str(lesson_dir), "--target", "staging", "--owner", "user", "--user-id", USER,
                      "--replace-chunks"], client_factory=lambda t: FakeClient())
    assert rc == 2


def test_main_check_subcommand(lesson_dir: Path, capsys):
    assert upload.main(["check", str(lesson_dir)]) == 0
    out = capsys.readouterr().out
    assert "[WARN] upload.lesson_without_questions @" in out and out.strip().endswith("0 errors, 1 warnings")
    assert upload.main(["check", str(lesson_dir), "--item", "g:nope"]) == 1
    assert "[ERROR] upload.item_missing @ g:nope" in capsys.readouterr().out


def test_main_plan_subcommand_is_a_dry_run(lesson_dir: Path, no_dotenv):
    no_dotenv.setenv("STAGING_SUPABASE_URL", "https://stg.supabase.co")
    no_dotenv.setenv("STAGING_SUPABASE_SERVICE_ROLE_KEY", "sk")
    client = FakeClient()
    rc = upload.main(["plan", str(lesson_dir), "--target", "staging", "--owner", "user", "--user-id", USER,
                      "--skip-validation"], client_factory=lambda t: client)
    assert rc == 0 and client.writes == 0
    assert not (lesson_dir / "upload_result.json").exists()


def test_main_enrich_subcommand(lesson_dir: Path, no_dotenv):
    no_dotenv.setenv("STAGING_SUPABASE_URL", "https://stg.supabase.co")
    no_dotenv.setenv("STAGING_SUPABASE_SERVICE_ROLE_KEY", "sk")
    client = FakeClient()
    run_upload(lesson_dir, client)
    rc = upload.main(["enrich", str(lesson_dir), "--target", "staging", "--skip-validation"], client_factory=lambda t: client)
    assert rc == 0 and len(client.rows("topic_cards")) == 2


def test_update_rerun_replaces_objectives_and_prerequisites(lesson_dir: Path):
    # A retry with --update must not duplicate objectives or re-insert a prerequisite
    # pair (topic_prerequisites has UNIQUE(topic_id, prerequisite_topic_id)).
    client = FakeClient()
    run_upload(lesson_dir, client)
    first = (len(client.rows("topic_learning_objectives")), len(client.rows("topic_prerequisites")))
    run_upload(lesson_dir, client, update_mode=True)
    run_upload(lesson_dir, client, update_mode=True)
    assert (len(client.rows("topic_learning_objectives")), len(client.rows("topic_prerequisites"))) == first
    pairs = [(r["topic_id"], r["prerequisite_topic_id"]) for r in client.rows("topic_prerequisites")]
    assert len(pairs) == len(set(pairs))


# ---------------------------------------------------------------------------
# Retries and interrupted uploads
# ---------------------------------------------------------------------------

ALL_TABLES = ("domains", "curriculum_levels", "books", "topics", "content_chunks", "topic_learning_objectives",
              "topic_prerequisites", "reading_questions", "topic_questions", "topic_cards", "topic_exercises")


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> list[float]:
    """No test waits for real: record the retry waits instead."""
    waits: list[float] = []
    monkeypatch.setattr(upload, "_sleep", waits.append)
    return waits


def blip() -> Exception:
    """The transport error that cut the prod upload of 2026-11-15."""
    return httpx.ReadError("[SSL: SSLV3_ALERT_BAD_RECORD_MAC] ssl/tls alert bad record mac (_ssl.c:2711)")


def storage_500() -> Exception:
    """The Storage reply that failed one image in the same upload."""
    return StorageApiError("database error, code: 42P10", "DatabaseError", 500)


def api_error(code, message="x") -> APIError:
    return APIError({"message": message, "code": code, "hint": None, "details": None})


def http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://x.supabase.co/storage/v1/object/b/p.png")
    return httpx.HTTPStatusError(f"{status}", request=request, response=httpx.Response(status, request=request))


def raised_while_handling(outer: Exception, inner: Exception, explicit: bool = False) -> Exception:
    """``outer`` as Python builds it when raised inside ``except inner`` (``from inner`` if explicit)."""
    try:
        try:
            raise inner
        except Exception as cause:
            if explicit:
                raise outer from cause
            raise outer
    except Exception as e:
        return e


def snapshot(client: FakeClient) -> dict[str, int]:
    return {table: len(client.rows(table)) for table in ALL_TABLES}


def assert_no_duplicates(client: FakeClient) -> None:
    for table in ALL_TABLES:
        ids = [r["id"] for r in client.rows(table)]
        assert len(ids) == len(set(ids)), table
    titles = [(r["title"], r["parent_topic_id"]) for r in client.rows("topics")]
    assert len(titles) == len(set(titles))
    pages = [(r["topic_id"], r["chunk_index"]) for r in client.rows("content_chunks")]
    assert len(pages) == len(set(pages))


def clean_snapshot(tmp_path: Path) -> dict[str, int]:
    """Row counts of an upload of the lesson fixture that met no failure."""
    client = FakeClient()
    run_upload(build_lesson_dir(tmp_path / "clean"), client)
    return snapshot(client)


def flat(text: str) -> str:
    return " ".join(text.split())


def blip_as_httpx_raises_it() -> Exception:
    """httpx.ReadError <- httpcore.ReadError <- ssl.SSLError, the chain of the real failure."""
    text = "[SSL: SSLV3_ALERT_BAD_RECORD_MAC] ssl/tls alert bad record mac (_ssl.c:2711)"
    inner = raised_while_handling(httpcore.ReadError(text), ssl.SSLError(1, text), explicit=True)
    return raised_while_handling(httpx.ReadError(text), inner, explicit=True)


@pytest.mark.parametrize("error", [
    blip(),
    blip_as_httpx_raises_it(),
    httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known"),
    httpx.ReadTimeout("The read operation timed out"),
    httpx.RemoteProtocolError("Server disconnected without sending a response."),
    httpx.LocalProtocolError("Invalid input ConnectionInputs.SEND_HEADERS in state ConnectionState.CLOSED"),
    ConnectionResetError(54, "Connection reset by peer"),
    TimeoutError("timed out"),
    api_error(502, "JSON could not be generated"),  # gateway page instead of JSON: postgrest reports the status
    api_error("PGRST002", "Could not query the database for the schema cache. Retrying."),
    api_error("PGRST000"), api_error("PGRST001"), api_error("PGRST003"),
    api_error("40001"), api_error("40P01"), api_error("57014"),
    api_error("08006"), api_error("53300"), api_error("57P01"),
    api_error(408), api_error(425), api_error(429), api_error(500), api_error(503), api_error(504), api_error(524),
    storage_500(),
    StorageApiError("slow down", "TooManyRequests", "429"),
    # storage3 file API, reply it cannot parse: reported as 400, real status only in the chain
    raised_while_handling(StorageApiError("Unable to parse error message", "InternalError", 400),
                          raised_while_handling(KeyError("message"), http_status_error(503)), explicit=True),
    # storage3 bucket API, HTML error page: a bare JSONDecodeError with the status in its context
    raised_while_handling(json.JSONDecodeError("Expecting value", "<html>", 0), http_status_error(502)),
    type("AuthRetryableError", (Exception,), {})("Bad Gateway"),
])
def test_is_transient_true(error):
    assert upload._is_transient(error) is True


@pytest.mark.parametrize("error", [
    api_error("23505", "duplicate key value violates unique constraint"),
    api_error("23503"), api_error("42501", "permission denied"), api_error("42P01"), api_error("PGRST205"),
    api_error("PGRST204"), api_error(400), api_error(401), api_error(404), api_error(413), api_error(501),
    api_error(None),
    # a certificate the client refuses will be refused again
    raised_while_handling(httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
                          ssl.SSLCertVerificationError(1, "certificate verify failed"), explicit=True),
    StorageApiError("The resource already exists", "Duplicate", 409),
    StorageApiError("Bucket not found", "not_found", "404"),
    raised_while_handling(StorageApiError("The resource already exists", "Duplicate", 409), http_status_error(400),
                          explicit=True),
    httpx.UnsupportedProtocol("Request URL is missing an 'http://' or 'https://' protocol."),
    ValueError("bad"), KeyError("id"), FileNotFoundError("img.png"), RuntimeError("upload failed"),
    UploadError("stop"),
    raised_while_handling(upload.TransientUploadError("gave up"), blip(), explicit=True),
])
def test_is_transient_false(error):
    assert upload._is_transient(error) is False


def test_retry_returns_after_transient_failures_with_doubling_waits(sleeps, capsys):
    outcomes = [blip(), api_error(503), "done"]

    def call():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert upload._retry("read things", call) == "done"
    assert sleeps == [1.0, 2.0]
    out = flat(capsys.readouterr().out)
    assert "read things" in out and "[SSL: SSLV3_ALERT_BAD_RECORD_MAC]" in out and "try 2 of 5" in out


def test_retry_raises_other_errors_at_once(sleeps):
    calls = []

    def call():
        calls.append(1)
        raise api_error("23505", "duplicate key")

    with pytest.raises(APIError):
        upload._retry("insert", call)
    assert len(calls) == 1 and sleeps == []


def test_retry_gives_up_after_all_attempts_with_capped_waits(sleeps, monkeypatch):
    monkeypatch.setattr(upload, "RETRY_MAX_DELAY", 4.0)
    calls = []

    def call():
        calls.append(1)
        raise blip()

    with pytest.raises(upload.TransientUploadError) as ei:
        upload._retry("insert into topics", call)
    assert len(calls) == upload.RETRY_ATTEMPTS == 5 and sleeps == [1.0, 2.0, 4.0, 4.0]
    assert ei.value.exit_code == 1 and isinstance(ei.value.__cause__, httpx.ReadError)
    assert "insert into topics failed 5 times in a row" in str(ei.value) and "ReadError" in str(ei.value)


def test_retry_never_repeats_an_upload_error(sleeps):
    def call():
        raise raised_while_handling(upload.TransientUploadError("inner gave up"), blip(), explicit=True)

    with pytest.raises(upload.TransientUploadError, match="inner gave up"):
        upload._retry("outer", call)
    assert sleeps == []


def test_insert_batch_resends_when_the_request_never_arrived():
    client = FakeClient()
    client.fail("topics", "insert", blip(), times=2)
    rows = [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]
    upload._insert_batch(client, "topics", rows)
    assert [r["id"] for r in client.rows("topics")] == ["a", "b"]
    assert client.attempts.count(("topics", "insert")) == 3


def test_insert_batch_does_not_resend_rows_whose_reply_was_lost():
    client = FakeClient()
    client.fail("topics", "insert", blip(), after_commit=True)
    rows = [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]
    upload._insert_batch(client, "topics", rows)
    assert [r["id"] for r in client.rows("topics")] == ["a", "b"]  # once, and no 23505 on the retry
    assert client.attempts == [("topics", "insert"), ("topics", "select")]


def test_insert_batch_sends_only_the_rows_that_are_missing():
    client = FakeClient()
    client.tables["topics"] = [{"id": "a", "title": "A"}]
    client.fail("topics", "insert", blip())
    upload._insert_batch(client, "topics", [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}])
    assert sorted(r["id"] for r in client.rows("topics")) == ["a", "b"]


def test_insert_batch_retries_a_failed_read_back():
    client = FakeClient()
    client.fail("topics", "insert", blip(), after_commit=True)
    client.fail("topics", "select", api_error(504, "JSON could not be generated"))
    upload._insert_batch(client, "topics", [{"id": "a", "title": "A"}])
    assert [r["id"] for r in client.rows("topics")] == ["a"]
    assert client.attempts == [("topics", "insert"), ("topics", "select"), ("topics", "select")]


@pytest.mark.parametrize("after_commit", [False, True])
def test_upload_survives_one_failure_on_every_request_kind(lesson_dir: Path, tmp_path: Path, after_commit: bool):
    client = FakeClient()
    for table in ALL_TABLES:
        for op in ("select", "insert", "update", "delete"):
            client.fail(table, op, blip(), after_commit=after_commit)
    client.fail("storage", "upload", storage_500(), times=2, after_commit=after_commit)
    result = run_upload(lesson_dir, client)
    assert snapshot(client) == clean_snapshot(tmp_path)
    assert_no_duplicates(client)
    assert (result["topics_inserted"], result["chunks_inserted"], result["topic_cards"]) == (4, 4, 2)
    assert sorted(path for path, _, _ in client.uploads) == ["adr-cur/img_adr_1.png", "adr-cur/img_adr_2.png"]
    adr = client.topic_id(ADR)
    page_1 = next(r for r in client.rows("content_chunks") if r["topic_id"] == adr and r["chunk_index"] == 1)
    assert "curriculum-images/adr-cur/img_adr_1.png" in page_1["content"]


@pytest.mark.parametrize("after_commit", [False, True])
def test_update_rerun_survives_one_failure_on_every_request_kind(lesson_dir: Path, tmp_path: Path, after_commit: bool):
    client = FakeClient()
    run_upload(lesson_dir, client)
    for table in ALL_TABLES:
        for op in ("select", "insert", "update", "delete"):
            client.fail(table, op, blip(), after_commit=after_commit)
    run_upload(lesson_dir, client, update_mode=True, replace_chunks=True)
    assert snapshot(client) == clean_snapshot(tmp_path)
    assert_no_duplicates(client)


def test_images_are_uploaded_before_any_database_write(lesson_dir: Path):
    client = FakeClient()
    run_upload(lesson_dir, client)
    first_write = next(i for i, (name, op) in enumerate(client.attempts) if name != "storage" and op != "select")
    last_image = max(i for i, (name, _) in enumerate(client.attempts) if name == "storage")
    assert last_image < first_write


def test_image_upload_that_keeps_failing_stops_before_the_database(lesson_dir: Path):
    client = FakeClient()
    client.fail("storage", "upload", storage_500(), times=5)
    with pytest.raises(upload.TransientUploadError) as ei:
        run_upload(lesson_dir, client)
    message = flat(str(ei.value))
    assert "upload image img_adr_1 failed 5 times in a row" in message and "42P10" in message
    assert "Nothing was written to the database. Run the same command again." in message
    assert "--update" not in message
    assert all(op == "select" for name, op in client.attempts if name != "storage")
    assert snapshot(client) == dict.fromkeys(ALL_TABLES, 0)
    assert [path for name, path in client.attempts if name == "storage"] == ["adr-cur/img_adr_1.png"] * 5
    assert not (lesson_dir / "upload_result.json").exists()


def test_image_stored_by_a_try_whose_reply_was_lost_counts_as_uploaded(lesson_dir: Path):
    client = FakeClient()
    client.fail("storage", "upload", blip(), after_commit=True, when=lambda path: path.endswith("img_adr_1.png"))
    run_upload(lesson_dir, client)
    assert sorted(path for path, _, _ in client.uploads) == ["adr-cur/img_adr_1.png", "adr-cur/img_adr_2.png"]
    adr = client.topic_id(ADR)
    page_1 = next(r for r in client.rows("content_chunks") if r["topic_id"] == adr and r["chunk_index"] == 1)
    assert "curriculum-images/adr-cur/img_adr_1.png" in page_1["content"] and "image:" not in page_1["content"]


def test_storage_bucket_probe_that_keeps_failing_stops_the_run(lesson_dir: Path, monkeypatch):
    client = FakeClient()

    def unreachable(name):
        raise blip()

    monkeypatch.setattr(client.storage, "get_bucket", unreachable)
    with pytest.raises(upload.TransientUploadError, match="read storage bucket failed 5 times"):
        run_upload(lesson_dir, client)
    assert client.uploads == [] and snapshot(client) == dict.fromkeys(ALL_TABLES, 0)


def interrupt_chunks(client: FakeClient, monkeypatch) -> None:
    """Two pages per request; the second request for pages fails until the run gives up."""
    monkeypatch.setattr(upload, "BATCH_SIZE", 2)
    client.fail("content_chunks", "insert", blip(), times=5, skip=1)


def test_interrupted_first_upload_says_how_to_finish_and_the_rerun_completes(lesson_dir: Path, tmp_path: Path, monkeypatch):
    expected = clean_snapshot(tmp_path)
    client = FakeClient()
    interrupt_chunks(client, monkeypatch)
    with pytest.raises(upload.TransientUploadError) as ei:
        run_upload(lesson_dir, client)
    message = flat(str(ei.value))
    assert "insert into content_chunks failed 5 times in a row" in message
    assert "The curriculum may be only partly uploaded." in message
    assert "Run the same command again with --update --replace-chunks to finish it." in message
    assert "Rows already written are reused, not duplicated." in message
    assert len(client.rows("topics")) == 4 and len(client.rows("content_chunks")) == 2  # half a curriculum
    assert client.rows("topic_cards") == [] and not (lesson_dir / "upload_result.json").exists()

    result = run_upload(lesson_dir, client, update_mode=True, replace_chunks=True)
    assert snapshot(client) == expected
    assert_no_duplicates(client)
    assert (result["topics_inserted"], result["topics_skipped"], result["chunks_inserted"]) == (0, 4, 4)
    assert (lesson_dir / "upload_result.json").exists()


def test_interrupted_first_upload_also_completes_with_update_alone(lesson_dir: Path, tmp_path: Path, monkeypatch):
    client = FakeClient()
    interrupt_chunks(client, monkeypatch)
    with pytest.raises(upload.TransientUploadError):
        run_upload(lesson_dir, client)
    kept = set(client.chunk_ids().values())
    run_upload(lesson_dir, client, update_mode=True)
    assert snapshot(client) == clean_snapshot(tmp_path)
    assert_no_duplicates(client)
    assert kept <= set(client.chunk_ids().values())  # the pages already written are kept


def test_plain_rerun_after_an_interrupted_upload_is_refused(lesson_dir: Path, monkeypatch):
    client = FakeClient()
    interrupt_chunks(client, monkeypatch)
    with pytest.raises(upload.TransientUploadError):
        run_upload(lesson_dir, client)
    before, writes, uploads = snapshot(client), client.writes, list(client.uploads)

    with pytest.raises(UploadError) as ei:
        run_upload(lesson_dir, client)
    message = flat(str(ei.value))
    assert not isinstance(ei.value, upload.TransientUploadError) and ei.value.exit_code == 1
    assert "already holds 4 of the 4 topics" in message and "--update --replace-chunks" in message
    assert snapshot(client) == before and client.writes == writes and client.uploads == uploads


def test_plain_upload_into_an_existing_domain_without_these_topics_still_works(lesson_dir: Path):
    client = FakeClient()
    client.tables["domains"] = [{"id": "d-old", "slug": "adr-cur", "domain_family": "adr-cur", "variant": "extensive"}]
    client.tables["topics"] = [
        {"id": "t-other", "domain_id": "d-old", "title": "Another Area", "depth": 0, "parent_topic_id": None},
        # same title as a lesson, but at another place in the tree: not the same topic
        {"id": "t-same-name", "domain_id": "d-old", "title": ADR, "depth": 1, "parent_topic_id": "t-other"},
    ]
    result = run_upload(lesson_dir, client)
    assert result["domain_id"] == "d-old" and result["topics_inserted"] == 4
    assert len(client.rows("topics")) == 6


def test_interrupted_update_says_to_run_the_same_command_again(lesson_dir: Path, tmp_path: Path):
    client = FakeClient()
    run_upload(lesson_dir, client)
    client.fail("topic_questions", "insert", blip(), times=5)
    with pytest.raises(upload.TransientUploadError) as ei:
        run_upload(lesson_dir, client, update_mode=True)
    message = flat(str(ei.value))
    assert "The update is incomplete. Run the same command again to finish it." in message
    assert "--replace-chunks" not in message
    assert client.rows("topic_questions") == []  # cleared, not yet written back

    run_upload(lesson_dir, client, update_mode=True)
    assert snapshot(client) == clean_snapshot(tmp_path)
    assert_no_duplicates(client)


def test_interrupted_enrich_says_to_run_it_again(lesson_dir: Path, tmp_path: Path):
    client = FakeClient()
    run_upload(lesson_dir, client)
    client.fail("topic_learning_objectives", "insert", blip(), times=5)
    with pytest.raises(upload.TransientUploadError) as ei:
        upload.enrich_curriculum(lesson_dir, client, TARGET, skip_validation=True)
    message = flat(str(ei.value))
    assert "The enrichment is incomplete" in message and "Run the same command again to finish it." in message

    upload.enrich_curriculum(lesson_dir, client, TARGET, skip_validation=True)
    assert snapshot(client) == clean_snapshot(tmp_path)
    assert_no_duplicates(client)


def test_enrich_that_cannot_reach_the_target_wrote_nothing(lesson_dir: Path):
    client = FakeClient()
    run_upload(lesson_dir, client)
    before = snapshot(client)
    client.fail("topics", "select", blip(), times=5)
    with pytest.raises(upload.TransientUploadError, match="Nothing was written to the database"):
        upload.enrich_curriculum(lesson_dir, client, TARGET, skip_validation=True)
    assert snapshot(client) == before


def test_main_reports_an_interrupted_upload_without_a_traceback(lesson_dir: Path, no_dotenv, capsys):
    no_dotenv.setenv("STAGING_SUPABASE_URL", "https://stg.supabase.co")
    no_dotenv.setenv("STAGING_SUPABASE_SERVICE_ROLE_KEY", "sk-staging")
    client = FakeClient()
    client.fail("topics", "insert", blip(), times=5, skip=1)
    rc = upload.main(["upload", str(lesson_dir), "--target", "staging", "--owner", "user", "--user-id", USER,
                      "--skip-validation"], client_factory=lambda t: client)
    out = flat(capsys.readouterr().out)
    assert rc == 1 and "Traceback" not in out and "sk-staging" not in out
    assert "Error: insert into topics failed 5 times in a row (ReadError: [SSL: SSLV3_ALERT_BAD_RECORD_MAC]" in out
    assert "Run the same command again with --update --replace-chunks to finish it." in out


def test_main_prints_error_text_that_looks_like_console_markup(lesson_dir: Path, no_dotenv, capsys, monkeypatch):
    def boom(args, client_factory):
        raise UploadError("odd text [/] and [ssl] kept as written")

    monkeypatch.setattr(upload, "_run", boom)
    assert upload.main(["check", str(lesson_dir)]) == 1
    assert "odd text [/] and [ssl] kept as written" in flat(capsys.readouterr().out)


def test_has_table_retries_a_schema_cache_outage_instead_of_calling_the_table_missing():
    outage = api_error("PGRST002", "Could not query the database for the schema cache. Retrying.")
    client = FakeClient()
    client.fail("topic_cards", "select", outage, times=2)
    assert upload.has_table(client, "topic_cards") is True

    client.fail("topic_cards", "select", outage, times=5)
    with pytest.raises(upload.TransientUploadError, match="probe table topic_cards failed 5 times"):
        upload.has_table(client, "topic_cards")


def test_upload_does_not_skip_practice_content_when_the_probe_keeps_failing(lesson_dir: Path):
    client = FakeClient()
    client.fail("topic_questions", "select", api_error("PGRST002", "Could not query the database for the schema cache."),
                times=5)
    with pytest.raises(upload.TransientUploadError, match="Nothing was written to the database"):
        run_upload(lesson_dir, client)
    assert client.writes == 0


def test_resolve_owner_stops_when_the_target_is_unreachable_instead_of_listing_users():
    client = FakeClient()
    client.rpc_results["find_user_by_email"] = blip()
    with pytest.raises(upload.TransientUploadError, match="resolve owner email failed 5 times"):
        upload.resolve_owner_id(client, user_email="me@example.com")
    assert len(client.rpc_calls) == 5 and client.list_users_calls == []


def test_resolve_owner_retries_the_user_listing(monkeypatch):
    client = FakeClient()
    client.rpc_results["find_user_by_email"] = api_error("PGRST202", "Could not find the function")
    client.users = [SimpleNamespace(id=USER, email="me@example.com")]
    real, failures = client.auth.admin.list_users, [blip(), blip()]

    def flaky(page=None, per_page=None):
        if failures:
            raise failures.pop()
        return real(page=page, per_page=per_page)

    monkeypatch.setattr(client.auth.admin, "list_users", flaky)
    assert upload.resolve_owner_id(client, user_email="me@example.com") == USER


def test_reads_are_complete_when_the_server_caps_a_response(lesson_dir: Path, tmp_path: Path, monkeypatch):
    # PostgREST returns at most max-rows rows per response and says nothing. Update mode
    # re-inserts every topic and page it did not see, so its reads must page to the end.
    client = FakeClient()
    run_upload(lesson_dir, client)
    client.max_rows = 3  # fewer than the 4 topics and the 4 pages
    existing, id_to_title = upload.find_existing_topics(client, client.rows("domains")[0]["id"])
    assert len(existing) == len(id_to_title) == 4
    assert len(upload.fetch_chunk_ids(client, [r["id"] for r in client.rows("topics")])) == 4

    for cap, page_size in ((3, 1000), (1, 1000), (1000, 2), (2, 2)):
        client.max_rows = cap
        monkeypatch.setattr(upload, "SELECT_PAGE_SIZE", page_size)
        run_upload(lesson_dir, client, update_mode=True)
        upload.enrich_curriculum(lesson_dir, client, TARGET, skip_validation=True)
        assert snapshot(client) == clean_snapshot(tmp_path / f"{cap}-{page_size}")
        assert_no_duplicates(client)


# ---------------------------------------------------------------------------
# Findings of the independent review (2026-11-16)
# ---------------------------------------------------------------------------


def unique_violation() -> APIError:
    return api_error("23505", 'duplicate key value violates unique constraint "topics_pkey"')


def test_retry_line_keeps_error_text_that_looks_like_console_markup(capsys):
    outcomes = [api_error(503, "upstream [/rest/v1/topics] timed out [bold]"), "done"]

    def call():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    assert upload._retry("read [topics]", call) == "done"
    out = flat(capsys.readouterr().out)
    assert "read [topics]" in out and "upstream [/rest/v1/topics] timed out [bold]" in out


def test_insert_batch_accepts_a_collision_with_its_own_first_try():
    # The first request times out while the database is still running it. The read-back sees
    # nothing yet, the batch is sent again, and that second insert collides with the first one,
    # which has committed in between: every row is there, so this is a success.
    client = FakeClient()
    client.fail("topics", "insert", httpx.ReadTimeout("The read operation timed out"))
    client.fail("topics", "insert", unique_violation(), after_commit=True)
    upload._insert_batch(client, "topics", [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}])
    assert sorted(r["id"] for r in client.rows("topics")) == ["a", "b"]


def test_insert_batch_still_raises_a_real_unique_violation_on_a_retry():
    client = FakeClient()
    client.fail("topics", "insert", blip())
    client.fail("topics", "insert", unique_violation())  # another constraint: the rows are not there
    with pytest.raises(APIError):
        upload._insert_batch(client, "topics", [{"id": "a", "title": "A"}])
    assert client.rows("topics") == []


def test_insert_batch_raises_a_unique_violation_when_only_part_of_the_batch_is_there():
    client = FakeClient()
    client.tables["topics"] = [{"id": "a", "title": "A"}]  # written by an earlier request
    client.fail("topics", "insert", blip())
    client.fail("topics", "insert", unique_violation())  # "b" hits another constraint and is not written
    with pytest.raises(APIError):
        upload._insert_batch(client, "topics", [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}])
    assert [r["id"] for r in client.rows("topics")] == ["a"]


def test_plain_rerun_is_refused_when_only_some_topics_exist(lesson_dir: Path):
    client = FakeClient()
    client.fail("topics", "insert", blip(), times=5, skip=2)
    with pytest.raises(upload.TransientUploadError):
        run_upload(lesson_dir, client)
    assert len(client.rows("topics")) == 2

    with pytest.raises(UploadError, match="already holds 2 of the 4 topics"):
        run_upload(lesson_dir, client)
    assert len(client.rows("topics")) == 2


def test_plan_reports_the_refusal_the_upload_would_meet(lesson_dir: Path, no_dotenv, capsys):
    no_dotenv.setenv("STAGING_SUPABASE_URL", "https://stg.supabase.co")
    no_dotenv.setenv("STAGING_SUPABASE_SERVICE_ROLE_KEY", "sk")
    client = FakeClient()
    run_upload(lesson_dir, client)
    writes = client.writes
    capsys.readouterr()
    args = [str(lesson_dir), "--target", "staging", "--owner", "user", "--user-id", USER, "--skip-validation"]
    assert upload.main(["plan", *args], client_factory=lambda t: client) == 1
    assert "already holds 4 of the 4 topics" in flat(capsys.readouterr().out) and client.writes == writes
    assert upload.main(["plan", *args, "--update"], client_factory=lambda t: client) == 0
    assert client.writes == writes


def test_storage_bucket_creation_that_keeps_failing_stops_the_run(lesson_dir: Path, monkeypatch):
    client = FakeClient()

    def missing(name):
        raise StorageApiError("Bucket not found", "not_found", "404")

    def unreachable(name, options=None):
        raise blip()

    monkeypatch.setattr(client.storage, "get_bucket", missing)
    monkeypatch.setattr(client.storage, "create_bucket", unreachable)
    with pytest.raises(upload.TransientUploadError, match="create storage bucket failed 5 times"):
        run_upload(lesson_dir, client)
    assert client.uploads == [] and snapshot(client) == dict.fromkeys(ALL_TABLES, 0)


def test_main_owner_lookup_that_keeps_failing_says_what_to_run(lesson_dir: Path, no_dotenv, capsys):
    no_dotenv.setenv("STAGING_SUPABASE_URL", "https://stg.supabase.co")
    no_dotenv.setenv("STAGING_SUPABASE_SERVICE_ROLE_KEY", "sk")
    client = FakeClient()
    client.rpc_results["find_user_by_email"] = blip()
    rc = upload.main(["upload", str(lesson_dir), "--target", "staging", "--owner", "user",
                      "--user-email", "me@example.com", "--skip-validation"], client_factory=lambda t: client)
    out = flat(capsys.readouterr().out)
    assert rc == 1 and "resolve owner email failed 5 times in a row" in out
    assert "Nothing was written to the database. Run the same command again." in out
    assert client.writes == 0


def test_resolve_domain_id_falls_back_to_the_slug_the_upload_used(lesson_dir: Path):
    client = FakeClient()
    client.tables["domains"] = [{"id": "d-by-name", "slug": "adr"}, {"id": "d-by-key", "slug": "adr-cur"}]
    assert upload._resolve_domain_id(client, lesson_dir, {"domain": "ADR Cur"}) == "d-by-key"
    assert upload._resolve_domain_id(client, lesson_dir, {}) == "d-by-name"  # no "domain": the directory name


# --- a stopped run must not leave an upload_result.json that says "uploaded" ---


def test_interrupted_update_removes_the_earlier_upload_result(lesson_dir: Path, tmp_path: Path):
    import status

    client = FakeClient()
    run_upload(lesson_dir, client)
    result_file = lesson_dir / "upload_result.json"
    assert result_file.exists()

    client.fail("topic_questions", "insert", blip(), times=5)
    with pytest.raises(upload.TransientUploadError) as ei:
        run_upload(lesson_dir, client, update_mode=True)
    assert "The earlier upload_result.json was removed, so this upload does not show as done." in flat(str(ei.value))
    assert not result_file.exists()  # the target has no quiz questions: not "uploaded"
    assert status.check_status(lesson_dir)["current_stage"] == "review"  # status.py no longer says "upload" is done

    run_upload(lesson_dir, client, update_mode=True)
    assert read_json(result_file)["update_mode"] == "update"
    assert snapshot(client) == clean_snapshot(tmp_path)


def test_interrupted_enrich_keeps_the_upload_result_it_reads_the_domain_from(lesson_dir: Path, tmp_path: Path):
    # No "domain" in the manifest and a renamed directory: the slug no longer leads to the
    # domain, so upload_result.json is the only way back to it.
    manifest = read_json(lesson_dir / "manifest.json")
    del manifest["domain"], manifest["domain_family"]
    write_json(lesson_dir / "manifest.json", manifest)
    client = FakeClient()
    uploaded = run_upload(lesson_dir, client)
    assert client.rows("domains")[0]["slug"] == "adr"
    moved = lesson_dir.rename(lesson_dir.with_name("adr-v2"))

    client.fail("topic_cards", "insert", blip(), times=5)
    with pytest.raises(upload.TransientUploadError) as ei:
        upload.enrich_curriculum(moved, client, TARGET, skip_validation=True)
    assert "The enrichment is incomplete" in str(ei.value) and "removed" not in str(ei.value)
    assert read_json(moved / "upload_result.json") == uploaded

    result = upload.enrich_curriculum(moved, client, TARGET, skip_validation=True)
    assert result["domain_id"] == uploaded["domain_id"]
    assert snapshot(client) == clean_snapshot(tmp_path)


def test_interrupted_enrich_that_had_begun_clearing_does_not_say_nothing_was_written(lesson_dir: Path):
    client = FakeClient()
    run_upload(lesson_dir, client)
    client.fail("topic_prerequisites", "delete", blip(), times=5)
    with pytest.raises(upload.TransientUploadError) as ei:
        upload.enrich_curriculum(lesson_dir, client, TARGET, skip_validation=True)
    assert "Nothing was written" not in str(ei.value) and "The enrichment is incomplete" in str(ei.value)
    assert client.rows("topic_learning_objectives") == []  # already cleared


def test_enrich_ignores_a_domain_id_recorded_for_another_target(lesson_dir: Path):
    prod = Target(name="default", url="https://prod.example.supabase.co", key="service-role-secret")
    client = FakeClient()  # stands for prod
    uploaded = upload.upload_curriculum(lesson_dir, client, prod, owner_type="user", user_id=USER, skip_validation=True)
    # the same output then went to staging: the file now holds the staging domain id
    write_json(lesson_dir / "upload_result.json", {**uploaded, "target": "staging", "domain_id": "staging-domain-id"})

    result = upload.enrich_curriculum(lesson_dir, client, prod, skip_validation=True)
    assert result["domain_id"] == uploaded["domain_id"] and result["topics_skipped"] == 4  # found by slug on prod
    assert len(client.rows("topic_cards")) == 2
    assert upload._resolve_domain_id(client, lesson_dir, {"domain": "nope"}) == uploaded["domain_id"]  # no target: as before


def test_interrupted_upload_keeps_a_result_recorded_for_another_target(lesson_dir: Path):
    client = FakeClient()
    run_upload(lesson_dir, client)  # staging
    recorded = {**read_json(lesson_dir / "upload_result.json"), "target": "default"}
    write_json(lesson_dir / "upload_result.json", recorded)  # ... then prod: the file is prod's record

    client.fail("topic_questions", "insert", blip(), times=5)
    with pytest.raises(upload.TransientUploadError) as ei:
        run_upload(lesson_dir, client, update_mode=True)  # a staging run stops
    assert "removed" not in str(ei.value)
    assert read_json(lesson_dir / "upload_result.json") == recorded


def test_upload_stopped_by_ctrl_c_after_writing_began_removes_the_earlier_upload_result(lesson_dir: Path):
    client = FakeClient()
    run_upload(lesson_dir, client)
    client.fail("topic_cards", "insert", KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        run_upload(lesson_dir, client, update_mode=True)
    assert not (lesson_dir / "upload_result.json").exists()


def test_domain_insert_whose_reply_and_read_backs_are_lost_is_not_reported_as_nothing_written(lesson_dir: Path):
    client = FakeClient()
    client.fail("domains", "insert", blip(), after_commit=True)
    client.fail("domains", "select", blip(), times=5, skip=2)  # the two reads before the insert go through
    with pytest.raises(upload.TransientUploadError) as ei:
        run_upload(lesson_dir, client)
    assert len(client.rows("domains")) == 1
    assert "Nothing was written" not in str(ei.value) and "may be only partly uploaded" in str(ei.value)


def test_update_leaves_the_objectives_of_another_curriculum_in_the_same_domain(lesson_dir: Path, monkeypatch):
    # Two curricula can share a domain (same "domain" in their manifests). Finishing an
    # interrupted upload of one must not clear what belongs to the other.
    client = FakeClient()
    client.tables["domains"] = [{"id": "d-old", "slug": "adr-cur", "domain_family": "adr-cur", "variant": "extensive"}]
    client.tables["topics"] = [
        {"id": "t-other", "domain_id": "d-old", "title": "Another Area", "depth": 0, "parent_topic_id": None},
        {"id": "t-other-leaf", "domain_id": "d-old", "title": "Another Lesson", "depth": 1, "parent_topic_id": "t-other"},
    ]
    client.tables["topic_learning_objectives"] = [{"id": "o-other", "topic_id": "t-other-leaf", "objective_text": "Keep me"}]
    client.tables["topic_prerequisites"] = [{"id": "p-other", "topic_id": "t-other-leaf", "prerequisite_topic_id": "t-other"}]

    interrupt_chunks(client, monkeypatch)
    with pytest.raises(upload.TransientUploadError, match="--update --replace-chunks"):
        run_upload(lesson_dir, client)
    for kwargs in ({"update_mode": True, "replace_chunks": True}, {"update_mode": True}):
        run_upload(lesson_dir, client, **kwargs)
        assert "o-other" in {r["id"] for r in client.rows("topic_learning_objectives")}
        assert "p-other" in {r["id"] for r in client.rows("topic_prerequisites")}
        assert len(client.rows("topic_learning_objectives")) == 1 + 3 and len(client.rows("topic_prerequisites")) == 1 + 1


def test_run_that_stops_before_writing_keeps_the_earlier_upload_result(lesson_dir: Path):
    client = FakeClient()
    run_upload(lesson_dir, client)
    before = read_json(lesson_dir / "upload_result.json")
    client.fail("storage", "upload", storage_500(), times=5)
    (lesson_dir / "extracted" / "images" / "img_new.png").write_bytes(b"\x89PNG new")
    chunks = read_json(lesson_dir / "chunks.json")
    chunks[0]["images"] = [{"id": "img_new", "local_path": "images/img_new.png", "alt_text": "New", "mime_type": "image/png"}]
    write_json(lesson_dir / "chunks.json", chunks)
    with pytest.raises(upload.TransientUploadError) as ei:
        run_upload(lesson_dir, client, update_mode=True)
    assert "Nothing was written to the database" in str(ei.value) and "removed" not in str(ei.value)
    assert read_json(lesson_dir / "upload_result.json") == before


def test_any_other_failure_after_writing_began_also_removes_the_earlier_upload_result(lesson_dir: Path, capsys):
    client = FakeClient()
    run_upload(lesson_dir, client)
    client.fail("topic_questions", "insert", api_error("23503", "violates foreign key constraint"))
    with pytest.raises(APIError):  # not transient: raised as it is
        run_upload(lesson_dir, client, update_mode=True)
    assert not (lesson_dir / "upload_result.json").exists()
    assert "upload_result.json removed" in flat(capsys.readouterr().out)


# --- raw pipeline: exercises.json chunks on a re-run ---


def exercise_pairs(client: FakeClient) -> list[tuple[str, int]]:
    return sorted((r["topic_id"], r["chunk_index"]) for r in client.rows("content_chunks")
                  if r["chunk_index"] >= upload.EXERCISE_CHUNK_OFFSET)


def run_raw(input_dir: Path, client: FakeClient, **kwargs) -> dict:
    return run_upload(input_dir, client, **kwargs)


def test_raw_update_run_does_not_add_the_exercises_a_second_time(curriculum_dir: Path):
    client = FakeClient()
    run_raw(curriculum_dir, client)
    clean = exercise_pairs(client)
    assert len(clean) == 5
    kept = {r["id"] for r in client.rows("content_chunks")}

    result = run_raw(curriculum_dir, client, update_mode=True)
    assert exercise_pairs(client) == clean and result["exercise_chunks"] == 0
    assert {r["id"] for r in client.rows("content_chunks")} == kept  # skipped, like the pages


def test_raw_replace_run_replaces_the_exercises(curriculum_dir: Path):
    client = FakeClient()
    run_raw(curriculum_dir, client)
    clean = exercise_pairs(client)
    old = {r["id"] for r in client.rows("content_chunks")}
    result = run_raw(curriculum_dir, client, update_mode=True, replace_chunks=True)
    assert exercise_pairs(client) == clean and result["exercise_chunks"] == 5
    assert old.isdisjoint(r["id"] for r in client.rows("content_chunks"))


def test_raw_interrupted_update_then_the_same_command_again_keeps_five_exercises(curriculum_dir: Path):
    client = FakeClient()
    run_raw(curriculum_dir, client)
    clean = exercise_pairs(client)
    client.fail("topic_prerequisites", "insert", blip(), times=5)
    with pytest.raises(upload.TransientUploadError, match="Run the same command again to finish it"):
        run_raw(curriculum_dir, client, update_mode=True)
    run_raw(curriculum_dir, client, update_mode=True)
    assert exercise_pairs(client) == clean
    assert len(client.rows("content_chunks")) == 15 and len(client.rows("topic_prerequisites")) == 1


@pytest.mark.parametrize("replace", [False, True])
@pytest.mark.parametrize("on_area", [False, True])
def test_raw_first_upload_interrupted_in_the_exercises_completes_without_duplicates(
        curriculum_dir: Path, monkeypatch, replace: bool, on_area: bool):
    if on_area:  # exercises attached to a depth-0 topic, which has no page of its own
        exercises = read_json(curriculum_dir / "exercises.json")
        for entry in exercises:
            entry["topic_path"] = entry["topic_path"][:1]
        write_json(curriculum_dir / "exercises.json", exercises)
    monkeypatch.setattr(upload, "BATCH_SIZE", 2)
    client = FakeClient()
    # 10 pages = 5 requests, then the exercises: the first pair goes through, the next request fails
    client.fail("content_chunks", "insert", blip(), times=5, skip=6)
    with pytest.raises(upload.TransientUploadError, match="--update --replace-chunks"):
        run_raw(curriculum_dir, client)
    assert len(exercise_pairs(client)) == 2

    run_raw(curriculum_dir, client, update_mode=True, replace_chunks=replace)
    pairs = exercise_pairs(client)
    assert len(pairs) == 5 and len(set(pairs)) == 5
    assert len(client.rows("content_chunks")) == 15 and len(client.rows("topics")) == 15
    assert_no_duplicates(client)


def test_raw_replace_run_replaces_exercises_attached_to_a_topic_without_pages(curriculum_dir: Path):
    exercises = read_json(curriculum_dir / "exercises.json")
    for entry in exercises:
        entry["topic_path"] = entry["topic_path"][:1]  # the depth-0 topic: insert_chunks never touches it
    write_json(curriculum_dir / "exercises.json", exercises)
    client = FakeClient()
    run_raw(curriculum_dir, client)
    old = {r["id"] for r in client.rows("content_chunks") if r["chunk_index"] >= upload.EXERCISE_CHUNK_OFFSET}
    assert len(old) == 5

    run_raw(curriculum_dir, client, update_mode=True, replace_chunks=True)
    new = {r["id"] for r in client.rows("content_chunks") if r["chunk_index"] >= upload.EXERCISE_CHUNK_OFFSET}
    assert len(new) == 5 and new.isdisjoint(old)
