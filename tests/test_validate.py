"""Tests for validate.py — one test per check family, driven by the clean fixture."""

from __future__ import annotations

from pathlib import Path

import validate
from validate import run_validation

from conftest import read_json, write_json


def ids(findings, severity=None):
    return [
        f.check_id for f in findings
        if severity is None or f.severity == severity
    ]


def errors(findings):
    return ids(findings, "error")


def warnings(findings):
    return ids(findings, "warning")


# ---------------------------------------------------------------------------
# Clean fixture
# ---------------------------------------------------------------------------

def test_clean_curriculum_passes(curriculum_dir: Path):
    findings = run_validation(curriculum_dir, "all")
    assert findings == [], [f"{f.check_id}: {f.location}: {f.message}" for f in findings]


def test_upload_stage_passes_when_approved(curriculum_dir: Path):
    findings = run_validation(curriculum_dir, "upload")
    assert errors(findings) == []


def test_enrich_stage_passes(curriculum_dir: Path):
    findings = run_validation(curriculum_dir, "enrich")
    assert errors(findings) == []


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def test_manifest_missing_required_key(curriculum_dir: Path):
    path = curriculum_dir / "manifest.json"
    manifest = read_json(path)
    del manifest["name"]
    write_json(path, manifest)
    assert "manifest.missing_key" in errors(run_validation(curriculum_dir, "manifest"))


def test_manifest_missing_family_is_warning(curriculum_dir: Path):
    path = curriculum_dir / "manifest.json"
    manifest = read_json(path)
    del manifest["domain_family"]
    write_json(path, manifest)
    findings = run_validation(curriculum_dir, "manifest")
    assert "manifest.missing_key" in warnings(findings)
    assert errors(findings) == []


def test_manifest_invalid_variant(curriculum_dir: Path):
    path = curriculum_dir / "manifest.json"
    manifest = read_json(path)
    manifest["variant"] = "gigantic"
    write_json(path, manifest)
    assert "manifest.invalid_variant" in errors(run_validation(curriculum_dir, "manifest"))


def test_manifest_source_missing_fields(curriculum_dir: Path):
    path = curriculum_dir / "manifest.json"
    manifest = read_json(path)
    manifest["sources"].append({"title": "No type or path"})
    write_json(path, manifest)
    assert "manifest.source_fields" in errors(run_validation(curriculum_dir, "manifest"))


# ---------------------------------------------------------------------------
# extracted
# ---------------------------------------------------------------------------

def test_extracted_unnormalized_title(curriculum_dir: Path):
    path = curriculum_dir / "extracted" / "widgets.json"
    data = read_json(path)
    data["sections"].append(
        {"title": "Chapter 3: Leftover", "content": "Some text here."}
    )
    write_json(path, data)
    assert "extracted.unnormalized_title" in warnings(
        run_validation(curriculum_dir, "extracted")
    )


def test_extracted_book_meta_title(curriculum_dir: Path):
    path = curriculum_dir / "extracted" / "widgets.json"
    data = read_json(path)
    data["sections"].append({"title": "Foreword", "content": "By someone famous."})
    write_json(path, data)
    assert "extracted.unnormalized_title" in warnings(
        run_validation(curriculum_dir, "extracted")
    )


def test_extracted_duplicate_title_across_sources(curriculum_dir: Path):
    path = curriculum_dir / "extracted" / "gadgets.json"
    data = read_json(path)
    data["sections"].append(
        {"title": "Intro to Widgets", "content": "Duplicate title content."}
    )
    write_json(path, data)
    assert "extracted.duplicate_title" in warnings(
        run_validation(curriculum_dir, "extracted")
    )


def test_extracted_empty_sections(curriculum_dir: Path):
    path = curriculum_dir / "extracted" / "widgets.json"
    data = read_json(path)
    data["sections"] = []
    write_json(path, data)
    assert "extracted.empty_sections" in errors(
        run_validation(curriculum_dir, "extracted")
    )


# ---------------------------------------------------------------------------
# exploration
# ---------------------------------------------------------------------------

def test_exploration_unresolved_critical_gap(curriculum_dir: Path):
    path = curriculum_dir / "exploration.json"
    data = read_json(path)
    data["gaps"].append({"concept": "Safety Standards", "severity": "critical"})
    write_json(path, data)
    assert "exploration.unresolved_critical_gap" in warnings(
        run_validation(curriculum_dir, "exploration")
    )


def test_exploration_resolved_critical_gap_passes(curriculum_dir: Path):
    path = curriculum_dir / "exploration.json"
    data = read_json(path)
    data["gaps"].append({
        "concept": "Safety Standards", "severity": "critical",
        "resolution": "user_accepted",
    })
    write_json(path, data)
    assert "exploration.unresolved_critical_gap" not in ids(
        run_validation(curriculum_dir, "exploration")
    )


def test_exploration_missing_source_analysis(curriculum_dir: Path):
    path = curriculum_dir / "exploration.json"
    data = read_json(path)
    del data["per_source_analysis"]["gadgets.json"]
    write_json(path, data)
    assert "exploration.missing_source" in warnings(
        run_validation(curriculum_dir, "exploration")
    )


# ---------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------

def test_structure_stale_ref(curriculum_dir: Path):
    path = curriculum_dir / "structure.json"
    structure = read_json(path)
    structure[0]["children"][0]["source_sections"] = ["Nonexistent Section"]
    write_json(path, structure)
    assert "structure.stale_ref" in errors(run_validation(curriculum_dir, "structure"))


def test_structure_orphan_section(curriculum_dir: Path):
    path = curriculum_dir / "extracted" / "widgets.json"
    data = read_json(path)
    data["sections"].append(
        {"title": "Spare Notes", "content": "Referenced by no topic."}
    )
    write_json(path, data)
    assert "structure.orphan_section" in warnings(
        run_validation(curriculum_dir, "structure")
    )


def test_structure_empty_leaf(curriculum_dir: Path):
    path = curriculum_dir / "structure.json"
    structure = read_json(path)
    leaf = structure[0]["children"][0]
    del leaf["source_sections"]
    leaf["title"] = "Matches No Section"
    write_json(path, structure)
    assert "structure.empty_leaf" in errors(run_validation(curriculum_dir, "structure"))


def test_structure_leaf_title_fallback_is_warning(curriculum_dir: Path):
    path = curriculum_dir / "structure.json"
    structure = read_json(path)
    # Leaf title equals an extracted section title; dropping source_sections
    # leaves the chunker's title fallback in play.
    del structure[0]["children"][0]["source_sections"]
    write_json(path, structure)
    findings = run_validation(curriculum_dir, "structure")
    assert "structure.leaf_title_fallback" in warnings(findings)
    assert "structure.empty_leaf" not in errors(findings)


def test_structure_invalid_depth(curriculum_dir: Path):
    path = curriculum_dir / "structure.json"
    structure = read_json(path)
    structure[0]["children"][0]["depth"] = 5
    write_json(path, structure)
    assert "structure.invalid_depth" in errors(run_validation(curriculum_dir, "structure"))


def test_structure_invalid_bloom(curriculum_dir: Path):
    path = curriculum_dir / "structure.json"
    structure = read_json(path)
    structure[0]["children"][0]["learning_objectives"][0]["bloom_level"] = "memorize"
    write_json(path, structure)
    assert "structure.invalid_bloom" in errors(run_validation(curriculum_dir, "structure"))


def test_structure_prereq_unknown_topic(curriculum_dir: Path):
    path = curriculum_dir / "structure.json"
    structure = read_json(path)
    structure[2]["prerequisites"].append({"topic": "Ghost Topic", "strength": "required"})
    write_json(path, structure)
    assert "structure.prereq_unknown_topic" in errors(
        run_validation(curriculum_dir, "structure")
    )


def test_structure_prereq_cycle(curriculum_dir: Path):
    path = curriculum_dir / "structure.json"
    structure = read_json(path)
    # Gadget Foundations already requires Widget Foundations; close the loop.
    structure[0]["prerequisites"] = [
        {"topic": "Gadget Foundations", "strength": "required"},
    ]
    write_json(path, structure)
    assert "structure.prereq_cycle" in errors(run_validation(curriculum_dir, "structure"))


def test_structure_prereq_transitive(curriculum_dir: Path):
    path = curriculum_dir / "structure.json"
    structure = read_json(path)
    # Chain: Advanced Tuning -> Gadget Foundations -> Widget Foundations,
    # plus the redundant direct link Advanced Tuning -> Widget Foundations.
    structure[4]["prerequisites"] = [
        {"topic": "Gadget Foundations", "strength": "required"},
        {"topic": "Widget Foundations", "strength": "required"},
    ]
    write_json(path, structure)
    assert "structure.prereq_transitive" in warnings(
        run_validation(curriculum_dir, "structure")
    )


def test_structure_prereq_order(curriculum_dir: Path):
    path = curriculum_dir / "structure.json"
    structure = read_json(path)
    structure[0]["prerequisites"] = [
        {"topic": "Advanced Tuning", "strength": "recommended"},
    ]
    write_json(path, structure)
    assert "structure.prereq_order" in warnings(
        run_validation(curriculum_dir, "structure")
    )


def test_structure_duplicate_title(curriculum_dir: Path):
    path = curriculum_dir / "structure.json"
    structure = read_json(path)
    structure[1]["children"][0]["title"] = structure[0]["children"][0]["title"]
    write_json(path, structure)
    assert "structure.duplicate_title" in warnings(
        run_validation(curriculum_dir, "structure")
    )


def test_structure_breadth(curriculum_dir: Path):
    path = curriculum_dir / "structure.json"
    structure = read_json(path)
    write_json(path, structure[:2])
    assert "structure.breadth" in warnings(run_validation(curriculum_dir, "structure"))


def test_structure_invalid_level(curriculum_dir: Path):
    path = curriculum_dir / "structure.json"
    structure = read_json(path)
    structure[0]["suggested_level"] = 7
    write_json(path, structure)
    assert "structure.invalid_level" in errors(run_validation(curriculum_dir, "structure"))


# ---------------------------------------------------------------------------
# chunks
# ---------------------------------------------------------------------------

def test_chunks_empty_topic(curriculum_dir: Path):
    path = curriculum_dir / "chunks.json"
    chunks = read_json(path)
    chunks = [c for c in chunks if c["topic_path"][-1] != "Gadget Safety"]
    write_json(path, chunks)
    findings = run_validation(curriculum_dir, "chunks")
    assert "chunks.empty_topic" in errors(findings)


def test_chunks_empty_content(curriculum_dir: Path):
    path = curriculum_dir / "chunks.json"
    chunks = read_json(path)
    chunks[0]["content"] = "   "
    write_json(path, chunks)
    assert "chunks.empty_content" in errors(run_validation(curriculum_dir, "chunks"))


def test_chunks_token_range_hard_error(curriculum_dir: Path):
    path = curriculum_dir / "chunks.json"
    chunks = read_json(path)
    chunks[0]["content"] = "Tiny."
    write_json(path, chunks)
    assert "chunks.token_range" in errors(run_validation(curriculum_dir, "chunks"))


def test_chunks_token_range_soft_warning(curriculum_dir: Path):
    from conftest import make_text
    path = curriculum_dir / "chunks.json"
    chunks = read_json(path)
    chunks[0]["content"] = make_text(120, seed="short chunk")
    write_json(path, chunks)
    findings = run_validation(curriculum_dir, "chunks")
    assert "chunks.token_range" in warnings(findings)
    assert "chunks.token_range" not in errors(findings)


def test_chunks_unknown_topic_path(curriculum_dir: Path):
    path = curriculum_dir / "chunks.json"
    chunks = read_json(path)
    chunks[0]["topic_path"] = ["Ghost Area", "Ghost Leaf"]
    write_json(path, chunks)
    assert "chunks.unknown_topic_path" in errors(run_validation(curriculum_dir, "chunks"))


def test_chunks_index_gap(curriculum_dir: Path):
    path = curriculum_dir / "chunks.json"
    chunks = read_json(path)
    chunks[0]["chunk_index"] = 4
    write_json(path, chunks)
    assert "chunks.index_gap" in warnings(run_validation(curriculum_dir, "chunks"))


def test_chunks_variant_uses_wider_range(curriculum_dir: Path):
    from conftest import make_text
    manifest_path = curriculum_dir / "manifest.json"
    manifest = read_json(manifest_path)
    manifest["variant"] = "classic"
    write_json(manifest_path, manifest)
    path = curriculum_dir / "chunks.json"
    chunks = read_json(path)
    chunks[0]["content"] = make_text(250, seed="variant chunk")
    write_json(path, chunks)
    assert "chunks.token_range" not in ids(run_validation(curriculum_dir, "chunks"))


# ---------------------------------------------------------------------------
# exercises
# ---------------------------------------------------------------------------

def test_exercises_unknown_topic_path(curriculum_dir: Path):
    path = curriculum_dir / "exercises.json"
    data = read_json(path)
    data[0]["topic_path"] = ["Nowhere", "Nothing"]
    write_json(path, data)
    assert "exercises.unknown_topic_path" in errors(
        run_validation(curriculum_dir, "exercises")
    )


def test_exercises_invalid_bloom(curriculum_dir: Path):
    path = curriculum_dir / "exercises.json"
    data = read_json(path)
    data[0]["exercises"][0]["bloom_level"] = "grok"
    write_json(path, data)
    assert "exercises.invalid_bloom" in errors(run_validation(curriculum_dir, "exercises"))


def test_exercises_few_hints(curriculum_dir: Path):
    path = curriculum_dir / "exercises.json"
    data = read_json(path)
    data[0]["exercises"][0]["hints"] = ["Only one hint"]
    write_json(path, data)
    assert "exercises.few_hints" in warnings(run_validation(curriculum_dir, "exercises"))


def test_exercises_empty_problem(curriculum_dir: Path):
    path = curriculum_dir / "exercises.json"
    data = read_json(path)
    data[0]["exercises"][0]["problem_statement"] = ""
    write_json(path, data)
    assert "exercises.empty_problem" in errors(run_validation(curriculum_dir, "exercises"))


# ---------------------------------------------------------------------------
# review / upload gate
# ---------------------------------------------------------------------------

def test_review_no_validation_key(curriculum_dir: Path):
    path = curriculum_dir / "review.json"
    review = read_json(path)
    del review["validation"]
    write_json(path, review)
    assert "review.no_validation" in warnings(run_validation(curriculum_dir, "review"))


def test_upload_blocks_when_not_approved(curriculum_dir: Path):
    path = curriculum_dir / "review.json"
    review = read_json(path)
    review["approved"] = False
    write_json(path, review)
    assert "upload.not_approved" in errors(run_validation(curriculum_dir, "upload"))


def test_upload_blocks_when_chunks_missing(curriculum_dir: Path):
    (curriculum_dir / "chunks.json").unlink()
    assert "chunks.file_missing" in errors(run_validation(curriculum_dir, "upload"))


def test_all_skips_missing_optional_stages(curriculum_dir: Path):
    (curriculum_dir / "exercises.json").unlink()
    (curriculum_dir / "condensation_plan.json").unlink()
    assert run_validation(curriculum_dir, "all") == []


# ---------------------------------------------------------------------------
# condensation
# ---------------------------------------------------------------------------

def test_condensation_missing_content(curriculum_dir: Path):
    path = curriculum_dir / "condensation_plan.json"
    plan = read_json(path)
    del plan["detailed"][0]["children"][1]["content"]
    write_json(path, plan)
    assert "condensation.missing_content" in errors(
        run_validation(curriculum_dir, "condensation")
    )


def test_condensation_unknown_source(curriculum_dir: Path):
    path = curriculum_dir / "condensation_plan.json"
    plan = read_json(path)
    plan["detailed"][0]["children"][0]["source_children"] = ["Imaginary Topic"]
    write_json(path, plan)
    assert "condensation.unknown_source" in errors(
        run_validation(curriculum_dir, "condensation")
    )


def test_condensation_unknown_tier(curriculum_dir: Path):
    path = curriculum_dir / "condensation_plan.json"
    plan = read_json(path)
    plan["mega"] = []
    write_json(path, plan)
    assert "condensation.unknown_tier" in warnings(
        run_validation(curriculum_dir, "condensation")
    )


def test_condensation_keep_with_no_chunks(curriculum_dir: Path):
    path = curriculum_dir / "condensation_plan.json"
    plan = read_json(path)
    # "Widget Foundations" is a parent title — valid in the structure, but the
    # chunk index keys leaf titles, so 'keep' would copy nothing.
    plan["detailed"][0]["children"][0]["source_children"] = ["Widget Foundations"]
    write_json(path, plan)
    assert "condensation.source_no_chunks" in warnings(
        run_validation(curriculum_dir, "condensation")
    )


# ---------------------------------------------------------------------------
# CLI behavior
# ---------------------------------------------------------------------------

def test_cli_exit_codes(curriculum_dir: Path):
    assert validate.main([str(curriculum_dir)]) == 0

    # Inject a warning-only issue: strict should fail, default should pass
    path = curriculum_dir / "exercises.json"
    data = read_json(path)
    data[0]["exercises"][0]["hints"] = []
    write_json(path, data)
    assert validate.main([str(curriculum_dir)]) == 0
    assert validate.main([str(curriculum_dir), "--strict"]) == 1

    # Inject an error
    structure = read_json(curriculum_dir / "structure.json")
    structure[0]["children"][0]["source_sections"] = ["Bogus"]
    write_json(curriculum_dir / "structure.json", structure)
    assert validate.main([str(curriculum_dir)]) == 1


def test_cli_writes_report(curriculum_dir: Path):
    assert validate.main([str(curriculum_dir), "--json"]) == 0
    report = read_json(curriculum_dir / "validation_report.json")
    assert report["summary"]["errors"] == 0
    assert report["summary"]["warnings"] == 0
    assert report["findings"] == []


# ---------------------------------------------------------------------------
# Lesson mode (manifest.pipeline = "lesson") — the lesson_curriculum_dir fixture
# is the integration test of the whole artifact contract
# ---------------------------------------------------------------------------

from conftest import LESSON_DM, LESSON_SL, make_text  # noqa: E402


def _fmt(findings):
    return [f"{f.severity} {f.check_id} @ {f.location}: {f.message}" for f in findings]


def test_lesson_fixture_is_clean_at_all_and_upload(lesson_curriculum_dir: Path):
    for stage in ("all", "upload"):
        findings = run_validation(lesson_curriculum_dir, stage)
        assert errors(findings) == [], _fmt(findings)
        assert warnings(findings) == [], _fmt(findings)


def test_lesson_fixture_every_family_runs_clean(lesson_curriculum_dir: Path):
    for stage in validate.LESSON_STAGES:
        findings = run_validation(lesson_curriculum_dir, stage)
        assert errors(findings) == [], (stage, _fmt(findings))


def test_raw_fixture_still_validates_and_rejects_lesson_families(curriculum_dir: Path):
    assert run_validation(curriculum_dir, "all") == []
    assert errors(run_validation(curriculum_dir, "upload")) == []
    assert errors(run_validation(curriculum_dir, "units")) == ["units.not_applicable"]


def test_manifest_invalid_pipeline_is_error(curriculum_dir: Path):
    path = curriculum_dir / "manifest.json"
    manifest = read_json(path)
    manifest["pipeline"] = "lecture"
    write_json(path, manifest)
    assert "manifest.invalid_pipeline" in errors(run_validation(curriculum_dir, "manifest"))


def test_manifest_missing_pipeline_is_warning(curriculum_dir: Path):
    path = curriculum_dir / "manifest.json"
    manifest = read_json(path)
    del manifest["pipeline"]
    write_json(path, manifest)
    findings = run_validation(curriculum_dir, "manifest")
    assert errors(findings) == []
    assert any(f.check_id == "manifest.missing_key" and "pipeline" in f.message for f in findings)


def test_lesson_families_delegate_to_stage_modules(lesson_curriculum_dir: Path):
    cur = lesson_curriculum_dir
    # qualify.check findings are split by prefix into units / qualification / concepts
    qual_path = cur / "qualification" / "peaa.json"
    qual = read_json(qual_path)
    first = next(iter(qual["units"].values()))
    first["concepts"][0]["quote"] = "not a substring of the unit"
    write_json(qual_path, qual)
    assert "qualification.quote_mismatch" in errors(run_validation(cur, "qualification"))
    assert "qualification.quote_mismatch" not in ids(run_validation(cur, "units"))
    assert "qualification.quote_mismatch" not in ids(run_validation(cur, "concepts"))
    concepts_path = cur / "concepts.json"
    concepts = read_json(concepts_path)
    concepts["concepts"][0]["label"] = "software"
    write_json(concepts_path, concepts)
    assert "concepts.too_broad" in warnings(run_validation(cur, "concepts"))
    # lessons.check behind the lessons family
    from contracts import item_id_to_filename
    lesson_path = cur / "lessons" / item_id_to_filename(LESSON_DM)
    lesson = read_json(lesson_path)
    lesson["pages"][1]["markdown"] = None
    write_json(lesson_path, lesson)
    assert "lessons.markdown_missing" in errors(run_validation(cur, "lessons"))
    assert "lessons.markdown_missing" in errors(run_validation(cur, "all"))
    # groups / syntheses / questions / cards families
    groups_path = cur / "groups.json"
    groups = read_json(groups_path)
    groups["groups"][0]["watch"] = []
    write_json(groups_path, groups)
    assert "groups.watch" in errors(run_validation(cur, "groups"))
    (cur / "syntheses" / item_id_to_filename(LESSON_SL)).unlink()
    assert "syntheses.missing" in errors(run_validation(cur, "syntheses"))
    q_path = cur / "questions" / item_id_to_filename(LESSON_SL)
    q = read_json(q_path)
    q["embedded"] = []
    write_json(q_path, q)
    assert "questions.none_reachable" in errors(run_validation(cur, "questions"))
    c_path = cur / "cards" / item_id_to_filename(LESSON_SL)
    c = read_json(c_path)
    c["cards"] = c["cards"][:2]
    write_json(c_path, c)
    assert "cards.count" in errors(run_validation(cur, "cards"))


def test_lesson_single_family_reports_missing_checkpoint(lesson_curriculum_dir: Path):
    import shutil
    shutil.rmtree(lesson_curriculum_dir / "cards")
    assert errors(run_validation(lesson_curriculum_dir, "cards")) == ["cards.file_missing"]
    # raw-only families still answer in lesson mode
    assert errors(run_validation(lesson_curriculum_dir, "exploration")) == ["exploration.file_missing"]
    assert "cards.file_missing" not in ids(run_validation(lesson_curriculum_dir, "all"))
    assert "cards.file_missing" in errors(run_validation(lesson_curriculum_dir, "upload"))


def test_lesson_critique_pending_is_warning(lesson_curriculum_dir: Path):
    path = lesson_curriculum_dir / "critique" / "summary.json"
    summary = read_json(path)
    summary["stages"]["lessons"]["by_item"][LESSON_DM]["status"] = "pending"
    del summary["stages"]["cards"]
    write_json(path, summary)
    for stage in ("critique", "all", "upload"):
        findings = run_validation(lesson_curriculum_dir, stage)
        pending = [f for f in findings if f.check_id == "critique.pending"]
        assert {f.location for f in pending} == {"critique/lessons/", "critique/cards/"}, _fmt(findings)
        assert all(f.severity == "warning" for f in pending)
        assert "critique.item_failed" not in ids(findings)
    assert errors(run_validation(lesson_curriculum_dir, "all")) == []


def test_lesson_failed_item_blocks_upload_only(lesson_curriculum_dir: Path):
    path = lesson_curriculum_dir / "critique" / "summary.json"
    summary = read_json(path)
    entry = summary["stages"]["questions"]["by_item"][LESSON_SL]
    entry.update(status="FAILED", round=2, critical_failed=[f"{LESSON_SL}#q1:q.answerable"])
    summary["stages"]["cards"]["by_item"][LESSON_DM]["status"] = "fail"
    write_json(path, summary)
    findings = run_validation(lesson_curriculum_dir, "all")
    assert errors(findings) == []
    assert warnings(findings).count("critique.item_failed") == 2
    findings = run_validation(lesson_curriculum_dir, "upload")
    failed = [f for f in findings if f.check_id == "critique.item_failed"]
    assert len(failed) == 2 and all(f.severity == "error" for f in failed)
    assert any("q.answerable" in f.message and "FAILED" in f.message for f in failed)


def test_lesson_critique_summary_shape_errors(lesson_curriculum_dir: Path):
    path = lesson_curriculum_dir / "critique" / "summary.json"
    path.write_text("{not json", encoding="utf-8")
    assert errors(run_validation(lesson_curriculum_dir, "critique")) == ["critique.invalid_json"]
    write_json(path, {"contract_version": 1})
    assert errors(run_validation(lesson_curriculum_dir, "critique")) == ["critique.shape"]


def test_lesson_upload_requires_practice_for_every_lesson(lesson_curriculum_dir: Path):
    from contracts import item_id_to_filename
    (lesson_curriculum_dir / "cards" / item_id_to_filename(LESSON_DM)).unlink()
    findings = run_validation(lesson_curriculum_dir, "all")
    assert errors(findings) == [] and "cards.missing" in warnings(findings)
    findings = run_validation(lesson_curriculum_dir, "upload")
    assert "cards.missing" in errors(findings)
    (lesson_curriculum_dir / "review.json").unlink()
    assert "upload.not_approved" in errors(run_validation(lesson_curriculum_dir, "upload"))


def test_lesson_structure_and_chunks_use_lesson_rules(lesson_curriculum_dir: Path):
    cur = lesson_curriculum_dir
    # Built structure: leaves carry source_sections [] and the curriculum has one area —
    # raw-mode coverage/breadth rules would fire, lesson mode skips them.
    assert validate.check_structure(cur) == []
    raw_view = validate.check_structure(cur, lesson_mode=False)
    assert "structure.empty_leaf" in errors(raw_view) and "structure.breadth" in warnings(raw_view)
    # Chunk token band: CHUNK_TOKENS_SOFT_LESSON (150-900) instead of 500-1500
    path = cur / "chunks.json"
    chunks = read_json(path)
    chunks[0]["content"] = make_text(1000, seed="long page")
    write_json(path, chunks)
    findings = run_validation(cur, "chunks")
    assert "chunks.token_range" in warnings(findings) and errors(findings) == []
    assert [f.location for f in findings if f.check_id == "chunks.token_range"][0].startswith("chunks.json[0]")
    raw_view = validate.check_chunks(cur, lesson_mode=False)  # 1000 tokens sits inside the raw 500-1500 band
    assert not any(f.location.startswith("chunks.json[0]") for f in raw_view if f.check_id == "chunks.token_range")
    chunks[0]["content"] = "A short page with only a few words on it. " * 8  # ~80 tokens: under 150, over the hard 50
    write_json(path, chunks)
    findings = run_validation(cur, "chunks")
    assert "chunks.token_range" in warnings(findings) and errors(findings) == []


def test_lesson_mode_runs_raw_only_families_when_present(lesson_curriculum_dir: Path):
    write_json(lesson_curriculum_dir / "exercises.json", [{"topic_path": ["Nowhere"], "exercises": [
        {"title": "x", "problem_statement": "", "hints": [], "expected_solution": "", "bloom_level": "apply", "difficulty": 1}]}])
    findings = run_validation(lesson_curriculum_dir, "all")
    assert {"exercises.unknown_topic_path", "exercises.empty_problem"} <= set(errors(findings))
    assert "exercises.unknown_topic_path" in errors(run_validation(lesson_curriculum_dir, "enrich"))


def test_lesson_cli(lesson_curriculum_dir: Path, capsys):
    assert validate.main([str(lesson_curriculum_dir)]) == 0
    assert validate.main([str(lesson_curriculum_dir), "--stage", "lessons", "--strict"]) == 0
    assert validate.main([str(lesson_curriculum_dir), "--stage", "upload", "--json"]) == 0
    report = read_json(lesson_curriculum_dir / "validation_report.json")
    assert report["summary"] == {**report["summary"], "stage": "upload", "errors": 0, "warnings": 0}
    capsys.readouterr()
    path = lesson_curriculum_dir / "critique" / "summary.json"
    summary = read_json(path)
    summary["stages"]["lessons"]["by_item"][LESSON_SL]["status"] = "fail"
    write_json(path, summary)
    assert validate.main([str(lesson_curriculum_dir)]) == 0
    assert validate.main([str(lesson_curriculum_dir), "--strict"]) == 1
    assert validate.main([str(lesson_curriculum_dir), "--stage", "upload"]) == 1
    assert "critique.item_failed" in capsys.readouterr().out
