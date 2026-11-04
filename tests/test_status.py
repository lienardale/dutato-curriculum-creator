"""Tests for status.py — raw output unchanged, lesson mode prints every stage with
progress counts, the critique summary, FAILED items and the RESUME marker."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import _compat  # noqa: F401

import status
from contracts import LESSON_STAGES, item_id_to_filename

from conftest import LESSON_DM, LESSON_SL, read_json, write_json


def _out(capsys) -> str:
    return capsys.readouterr().out


def _line(out: str, needle: str) -> str:
    matches = [ln for ln in out.splitlines() if needle in ln]
    assert len(matches) == 1, (needle, out)
    return matches[0]


# ---------------------------------------------------------------------------
# raw mode
# ---------------------------------------------------------------------------


def test_raw_status_output_unchanged(curriculum_dir: Path, capsys):
    result = status.check_status(curriculum_dir)
    assert "mode" not in result
    assert list(result["stages"]) == [name for name, _, _ in status.STAGES]
    assert result["next_stage"] == "upload" and result["stages"]["review"]["approved"] is True
    status.print_status(curriculum_dir)
    out = _out(capsys)
    assert "Pipeline Status:" in out and "Pipeline:" not in out
    assert _line(out, "Sources extracted").endswith("(2 files)")
    assert _line(out, "Quality reviewed").endswith("— approved")
    assert _line(out, "Uploaded to Supabase").endswith("<-- RESUME HERE")
    assert out.rstrip().endswith("Next step: upload")


def test_missing_directory(tmp_path: Path, capsys):
    result = status.check_status(tmp_path / "nope")
    assert result["exists"] is False and result["next_stage"] == "manifest"
    status.print_status(tmp_path / "nope")
    assert "Directory not found" in _out(capsys)


# ---------------------------------------------------------------------------
# lesson mode
# ---------------------------------------------------------------------------


def test_lesson_status_lists_every_stage_with_counts(lesson_curriculum_dir: Path, capsys):
    result = status.check_status(lesson_curriculum_dir)
    assert result["mode"] == "lesson" and result["failed_items"] == []
    names = [name for name, _, _, _ in LESSON_STAGES]
    assert set(names) <= set(result["stages"]) and "critique" in result["stages"]
    assert list(result["stages"]).index("critique") == names.index("cards") + 1  # inserted after cards
    assert result["next_stage"] == "upload"
    status.print_status(lesson_curriculum_dir)
    out = _out(capsys)
    assert "Pipeline Status (lesson):" in out and "Pipeline:   lesson" in out
    for _name, _artifact, description, _optional in LESSON_STAGES:
        assert description in out, description
    assert _line(out, "Sources extracted").endswith("(2 files, 1 images)")
    assert "(2/2 sources, 14 units)" in _line(out, "Units segmented")
    assert "(2/2 sources, 14/14 units)" in _line(out, "Units qualified")
    assert "(4 concepts)" in _line(out, "Concept registry")
    assert "2 groups, budgets filled, 1/1 prerequisites committed" in _line(out, "Lesson candidates grouped")
    assert "(2/2 groups)" in _line(out, "Knowledge briefs")
    assert "2/2 full, 0 outline; critique 2 pass" in _line(out, "Lessons designed")
    assert "(13 chunks)" in _line(out, "structure.json + chunks.json built")  # 6 DM + 7 SL pages
    assert "(2/2 lessons)" in _line(out, "Questions designed")
    assert "(2/2 lessons)" in _line(out, "Cards designed")
    critique_line = _line(out, "Critiqued by independent critics")
    for stage in ("qualification", "groups", "syntheses", "lessons", "questions", "cards"):
        assert f"{stage}: 2 pass" in critique_line
    assert _line(out, "Quality reviewed").endswith("(approved)")
    assert _line(out, "Uploaded to Supabase").endswith("<-- RESUME HERE")
    assert "FAILED items" not in out and out.rstrip().endswith("Next step: upload")


def test_lesson_resume_marker_at_first_incomplete_stage(lesson_curriculum_dir: Path, capsys):
    cur = lesson_curriculum_dir
    for path in (cur / "lessons").glob("*.json"):
        path.unlink()
    result = status.check_status(cur)
    assert result["next_stage"] == "lessons"
    assert result["stages"]["lessons"]["complete"] is False
    assert result["stages"]["questions"]["complete"] is False  # no lessons -> nothing to count
    status.print_status(cur)
    out = _out(capsys)
    assert "0/2 full, 0 outline" in _line(out, "Lessons designed")
    assert _line(out, "Lessons designed").endswith("<-- RESUME HERE")
    assert "RESUME HERE" not in _line(out, "Uploaded to Supabase")
    # outline-only lessons count separately; budgets missing hold the regroup stage
    lesson = {"lesson_id": LESSON_DM, "pages": [{"index": 0, "markdown": None}]}
    write_json(cur / "lessons" / item_id_to_filename(LESSON_DM), lesson)
    groups = read_json(cur / "groups.json")
    groups["groups"][0]["budget"] = None
    write_json(cur / "groups.json", groups)
    status.print_status(cur)
    out = _out(capsys)
    assert "0/2 full, 1 outline" in _line(out, "Lessons designed")
    assert "budgets 1/2" in _line(out, "Lesson candidates grouped")
    assert _line(out, "Lesson candidates grouped").endswith("<-- RESUME HERE")


def test_lesson_critique_pending_and_failed_items(lesson_curriculum_dir: Path, capsys):
    cur = lesson_curriculum_dir
    path = cur / "critique" / "summary.json"
    summary = read_json(path)
    summary["stages"]["questions"]["by_item"][LESSON_SL].update(status="FAILED", round=2)
    summary["stages"]["questions"]["FAILED"] = 1
    summary["stages"]["questions"]["pass"] = 1
    summary["stages"]["cards"]["by_item"][LESSON_DM]["status"] = "pending"
    summary["stages"]["cards"]["pending"] = 1
    summary["stages"]["cards"]["pass"] = 1
    del summary["stages"]["syntheses"]
    write_json(path, summary)
    result = status.check_status(cur)
    assert result["failed_items"] == [{"stage": "questions", "item_id": LESSON_SL, "round": 2}]
    assert result["stages"]["critique"]["complete"] is False
    assert result["next_stage"] == "critique"
    status.print_status(cur)
    out = _out(capsys)
    line = _line(out, "Critiqued by independent critics")
    assert "syntheses: no verdicts checked" in line and "questions: 1 pass, 1 FAILED" in line
    assert "cards: 1 pending, 1 pass" in line and line.endswith("<-- RESUME HERE")
    assert "FAILED items" in out and f"questions: {LESSON_SL} (round 2)" in out


def test_lesson_status_without_critique_summary(lesson_curriculum_dir: Path, capsys):
    shutil.rmtree(lesson_curriculum_dir / "critique")
    status.print_status(lesson_curriculum_dir)
    out = _out(capsys)
    line = _line(out, "Critiqued by independent critics")
    assert "lessons: no verdicts checked" in line and line.endswith("<-- RESUME HERE")
    assert "(2/2 full, 0 outline)" in _line(out, "Lessons designed")


def test_lesson_scope_narrows_extracted_sources(lesson_curriculum_dir: Path):
    cur = lesson_curriculum_dir
    write_json(cur / "extracted" / "extra.json", {"source_type": "web", "title": "extra", "sections": []})
    assert status.check_status(cur)["stages"]["qualify"]["complete"] is False
    write_json(cur / "scope.json", {"include_sources": ["peaa", "clean-arch"], "include_sections": {}})
    result = status.check_status(cur)
    assert result["stages"]["qualify"]["complete"] is True
    assert "2/2 sources" in result["stages"]["qualify"]["detail"]


def test_main_lists_curricula_and_prints_one(lesson_curriculum_dir: Path, monkeypatch, capsys):
    monkeypatch.setattr(status.sys, "argv", ["status.py", str(lesson_curriculum_dir)])
    status.main()
    assert "Pipeline Status (lesson):" in _out(capsys)
    monkeypatch.setattr(status, "__file__", str(lesson_curriculum_dir.parent / "status.py"))
    (lesson_curriculum_dir.parent / "output").mkdir()
    shutil.copytree(lesson_curriculum_dir, lesson_curriculum_dir.parent / "output" / "arch")
    monkeypatch.setattr(status.sys, "argv", ["status.py"])
    try:
        status.main()
    except SystemExit as e:
        assert e.code == 0
    out = _out(capsys)
    assert "Available curricula:" in out and "arch/  (Arch) — next: upload" in out
