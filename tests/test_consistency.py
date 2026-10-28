"""Cross-module consistency: producer/consumer pairs agree on file names, ids and
module signatures (docs/artifacts.md). Each test pins one agreement that no single
stage test would catch because it spans two modules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import _compat  # noqa: F401

import cards
import critique
import lessons
import qualify
import questions
import status
import upload
import validate
from conftest import LESSON_DM, LESSON_SL, read_json, write_json
from contracts import GROUP_ID_RE, LESSONS_DIR, filename_to_item_id, item_id_to_filename

ANCHOR = {"lesson_id": LESSON_DM}


def _errors(findings) -> list[str]:
    return sorted({f.check_id for f in findings if f.severity == "error"})


# ---------------------------------------------------------------------------
# lessons/anchor.json (lessons.py exemplar pointer) is not a lesson for any consumer
# ---------------------------------------------------------------------------


def test_item_file_rule_round_trips_group_ids():
    for gid in (LESSON_DM, LESSON_SL, "g:a", "g:event-sourcing-2"):
        assert filename_to_item_id(item_id_to_filename(gid)) == gid
        assert GROUP_ID_RE.match(gid)
    # the anchor pointer never maps back to a group id
    assert not GROUP_ID_RE.match(filename_to_item_id("anchor.json"))


def test_anchor_is_ignored_by_every_lessons_consumer(lesson_curriculum_dir: Path):
    cur = lesson_curriculum_dir
    write_json(cur / LESSONS_DIR / "anchor.json", ANCHOR)
    before_q = read_json(cur / "critique" / "summary.json")

    # lessons.py: not a lesson file; prepare hands the exemplar to the other lesson
    assert _errors(lessons.check(cur)) == []
    assert "anchor" not in {p.name for p in lessons.load_context(cur).lesson_files()}

    # questions.py / cards.py: lesson ids come from lessons/ — the anchor is not one
    assert questions.load_context(cur).lesson_ids() == [LESSON_DM, LESSON_SL]
    assert cards.load_context(cur).lesson_ids() == [LESSON_DM, LESSON_SL]
    assert _errors(questions.check(cur)) == []
    assert _errors(cards.check(cur)) == []
    assert not any("anchor" in f.location for f in questions.check(cur) + cards.check(cur))
    written, findings = questions.prepare(cur, force=True)
    assert {p.name for p in written} == {item_id_to_filename(LESSON_DM), item_id_to_filename(LESSON_SL)}
    assert not any("anchor" in f.location for f in findings)
    written, findings = cards.prepare(cur, force=True)
    assert {p.name for p in written} == {item_id_to_filename(LESSON_DM), item_id_to_filename(LESSON_SL)}

    # status.py: lesson counts and the questions/cards completeness ignore it
    result = status.check_status(cur)
    assert result["stages"]["lessons"]["complete"] is True
    assert result["stages"]["questions"]["complete"] is True
    assert result["stages"]["cards"]["complete"] is True
    assert result["next_stage"] == "upload"

    # critique.py: no critique work file / verdict is expected for "anchor"
    inputs = critique._Inputs(cur, [])
    assert critique._list_item_ids(inputs, "lessons") == [LESSON_DM, LESSON_SL]
    findings = critique.prepare(cur, "lessons")
    assert not any("anchor" in f.location for f in findings)
    assert not (cur / "critique" / "lessons" / "work" / "anchor.json").exists()
    assert _errors(critique.check(cur, "lessons")) == []
    assert read_json(cur / "critique" / "summary.json")["stages"]["lessons"]["items"] == before_q["stages"]["lessons"]["items"]

    # upload.py: practice content is keyed by lesson id; the anchor adds no lesson
    practice = upload.load_practice_content(cur)
    assert set(practice.lessons) == {LESSON_DM, LESSON_SL}
    assert practice.lessons[LESSON_DM].title  # not overwritten by the anchor file's missing title
    assert _errors(upload.check(cur)) == []

    # validate.py: whole-pipeline validation stays clean
    assert _errors(validate.run_validation(cur, "upload")) == []


def test_validate_artifact_presence_ignores_helper_files(tmp_path: Path):
    cur = tmp_path / "cur"
    for stage in ("lessons", "questions", "cards", "syntheses"):
        write_json(cur / stage / "anchor.json", ANCHOR)
    for stage in ("lessons", "questions", "cards", "syntheses"):
        assert validate._lesson_artifact_present(cur, stage) is False
        assert validate._critiqued_artifacts_present(cur, stage) is False
    write_json(cur / "lessons" / item_id_to_filename(LESSON_DM), {"lesson_id": LESSON_DM})
    assert validate._lesson_artifact_present(cur, "lessons") is True
    assert validate._critiqued_artifacts_present(cur, "lessons") is True
    # qualification files are keyed by source, not by g:<slug>
    write_json(cur / "qualification" / "peaa.json", {"source": "peaa", "units": {}})
    assert validate._lesson_artifact_present(cur, "qualification") is True
    assert validate._critiqued_artifacts_present(cur, "qualification") is True


# ---------------------------------------------------------------------------
# Module API: every stage exposes check(input_dir, **opts) / check_item(input_dir, item_id, **opts)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", [qualify, lessons, questions, cards, critique, upload])
def test_check_accepts_unknown_keyword_options(module, lesson_curriculum_dir: Path):
    assert isinstance(module.check(lesson_curriculum_dir, unknown_option=True), list)
    item = "peaa" if module is qualify else LESSON_DM
    assert isinstance(module.check_item(lesson_curriculum_dir, item, unknown_option=True), list)


def test_regroup_and_synthesize_check_accept_unknown_keyword_options(lesson_curriculum_dir: Path):
    import regroup
    import synthesize

    assert isinstance(regroup.check(lesson_curriculum_dir, unknown_option=True), list)
    assert isinstance(regroup.check_item(lesson_curriculum_dir, LESSON_DM, unknown_option=True), list)
    assert isinstance(synthesize.check(lesson_curriculum_dir, unknown_option=True), list)
    assert isinstance(synthesize.check_item(lesson_curriculum_dir, LESSON_DM, unknown_option=True), list)


# ---------------------------------------------------------------------------
# CLI: qualify.py scope-from-structure defaults --extracted to output/<name>/extracted/
# ---------------------------------------------------------------------------


def test_scope_from_structure_defaults_extracted_next_to_scope(lesson_curriculum_dir: Path, capsys):
    cur = lesson_curriculum_dir
    source = next(p for p in (cur / "extracted").glob("*.json"))
    title = read_json(source)["sections"][0]["title"]
    legacy = cur / "legacy" / "structure.json"
    write_json(legacy, [{"title": "Area One", "depth": 0, "children": [
        {"title": "Leaf", "depth": 1, "source_sections": [title], "children": []},
    ]}])
    rc = qualify.main(["scope-from-structure", str(legacy), "--area", "Area One", "-o", str(cur / "scope.json")])
    assert rc == 0, capsys.readouterr().out
    scope = json.loads((cur / "scope.json").read_text(encoding="utf-8"))
    assert scope["include_sources"] == [source.name[:-5]]
    assert scope["include_sections"] == {source.name[:-5]: [title]}


def test_status_help_exits_zero(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["status.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        status.main()
    assert exc.value.code == 0
    assert "Usage:" in capsys.readouterr().out
