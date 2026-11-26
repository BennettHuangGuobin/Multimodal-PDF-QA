import json

import pytest

from multimodal_pdf_qa.audit import audit_document, manifest_diagnostics
from multimodal_pdf_qa.prepare import prepare_pdf


@pytest.mark.parametrize("field,value", [
    ("schema_version", "unsupported"), ("expected_page_count", 999),
    ("successful_render_count", 0), ("failed_render_count", 999),
    ("pending_render_count", -1), ("status", "unknown"),
    ("successful_render_count", True), ("failed_render_count", None),
])
def test_manifest_tampering_is_reported(sample_pdf, tmp_path, field, value):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1-2")
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    audit = audit_document(directory)
    assert not audit["ok"]
    assert audit["render_complete"]
    assert any(f"manifest.{field}" in item for item in audit["issues"])


@pytest.mark.parametrize("field", ["schema_version", "expected_page_count", "successful_render_count",
                                  "failed_render_count", "status"])
def test_missing_manifest_summary_field_is_reported(sample_pdf, tmp_path, field):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1")
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    del manifest[field]
    path.write_text(json.dumps(manifest))
    audit = audit_document(directory)
    assert not audit["ok"]
    assert any(f"manifest.{field}" in item for item in audit["issues"])


@pytest.mark.parametrize("status", ["running", "incomplete"])
def test_recoverable_status_is_not_itself_a_failure(sample_pdf, tmp_path, status):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1")
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["status"] = status
    path.write_text(json.dumps(manifest))
    audit = audit_document(directory)
    assert audit["ok"] and audit["render_complete"]
    assert audit["manifest_status"] == status
    assert audit["issues"] == []
    assert any(f"status={status}" in item for item in audit["warnings"])
    prepare_pdf(sample_pdf, directory.parent, pages="1")
    assert json.loads(path.read_text())["status"] == "complete"


def test_phase1_running_manifest_can_omit_counts(sample_pdf, tmp_path):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1")
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["status"] = "running"
    for field in ("successful_render_count", "failed_render_count", "pending_render_count"):
        del manifest[field]
    path.write_text(json.dumps(manifest))
    audit = audit_document(directory)
    assert audit["ok"]
    assert any("absent in running checkpoint" in item for item in audit["warnings"])


def test_complete_status_cannot_hide_missing_png(sample_pdf, tmp_path):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1")
    (directory / "pages/page_0001.png").unlink()
    audit = audit_document(directory)
    assert not audit["ok"] and not audit["render_complete"]
    assert audit["index_render_counts"]["successful_render_count"] == 1
    assert audit["successful_render_count"] == 0
    assert any("manifest.status=complete contradicts" in item for item in audit["issues"])


def test_render_completion_does_not_imply_ocr_success(sample_pdf, tmp_path):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1")
    path = directory / "pages.jsonl"
    row = json.loads(path.read_text())
    row["ocr_status"] = "failed"
    row["errors"] = [{"stage": "ocr", "message": "synthetic failure", "error_type": "SyntheticError"}]
    path.write_text(json.dumps(row) + "\n")
    prepare_pdf(sample_pdf, directory.parent, pages="1")
    audit = audit_document(directory)
    assert audit["render_complete"] and not audit["ok"]
    assert audit["manifest_status"] == "complete"
    assert audit["ocr_failed_pages"] == [1]
    assert audit["invalid_parser_records"] == []


def test_incomplete_status_does_not_hide_bad_counts(sample_pdf, tmp_path):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1")
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest.update(status="incomplete", failed_render_count=99)
    path.write_text(json.dumps(manifest))
    audit = audit_document(directory)
    assert not audit["ok"]
    assert any("failed_render_count" in item for item in audit["issues"])


@pytest.mark.parametrize("recorded,observed", [
    ((0, 0, 3), (1, 0, 2)),  # pending -> success
    ((0, 0, 3), (0, 1, 2)),  # pending -> failed
    ((0, 1, 2), (1, 0, 2)),  # failed -> success
    ((1, 0, 2), (0, 1, 2)),  # success -> failed (forced render)
])
@pytest.mark.parametrize("legacy_pending", [False, True])
def test_running_allows_only_one_completed_page_transition(recorded, observed, legacy_pending):
    fields = ("successful_render_count", "failed_render_count", "pending_render_count")
    manifest = dict(schema_version="1.0", status="running", expected_page_count=3,
                    **dict(zip(fields, recorded)))
    if legacy_pending:
        del manifest["pending_render_count"]
    issues, warnings = manifest_diagnostics(manifest, {1, 2, 3}, dict(zip(fields, observed)), False)
    assert issues == []
    assert any("one-page checkpoint lag" in item for item in warnings)


@pytest.mark.parametrize("recorded,observed", [
    ((0, 0, 3), (2, 0, 1)),  # two pending pages completed: impossible between checkpoints
    ((0, 3, 0), (2, 1, 0)),  # two failed pages retried
    ((1, 0, 2), (0, 0, 3)),  # success never transitions to not_run in a page checkpoint
    ((0, 1, 2), (0, 0, 3)),  # failed never transitions to not_run
    ((0, 0, 2), (1, 0, 2)),  # counts do not conserve planned pages
    ((0, 0, 3), (1, 0, 1)),  # missing index row cannot be explained as a completed page
])
def test_running_impossible_count_changes_are_issues(recorded, observed):
    fields = ("successful_render_count", "failed_render_count", "pending_render_count")
    manifest = dict(schema_version="1.0", status="running", expected_page_count=3,
                    **dict(zip(fields, recorded)))
    issues, _ = manifest_diagnostics(manifest, {1, 2, 3}, dict(zip(fields, observed)), False)
    assert any("not a valid one-page checkpoint lag" in item for item in issues)


def test_running_large_lag_fails_audit_but_prepare_can_recover(sample_pdf, tmp_path):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1-3")
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest.update(status="running", successful_render_count=0, pending_render_count=3)
    path.write_text(json.dumps(manifest))
    audit = audit_document(directory)
    assert audit["render_complete"] and not audit["ok"]
    assert any("not a valid one-page checkpoint lag" in item for item in audit["issues"])
    prepare_pdf(sample_pdf, directory.parent, pages="1-3")
    assert audit_document(directory)["ok"]
