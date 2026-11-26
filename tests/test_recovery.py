import json

import pytest

from multimodal_pdf_qa.audit import audit_document
from multimodal_pdf_qa.prepare import prepare_pdf


def snapshot(directory):
    return {p.relative_to(directory): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


@pytest.mark.parametrize("damage", ["missing_status", "invalid_status", "bad_list", "bad_dimensions",
                                    "bad_matrix", "escape", "nonfinite", "invalid_page"])
def test_unrequested_corrupt_record_rejected_before_writes(sample_pdf, tmp_path, damage):
    output = tmp_path / "out"
    directory = prepare_pdf(sample_pdf, output, pages="1-2")
    path = directory / "pages.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    row = rows[1]
    if damage == "missing_status":
        del row["render_status"]
    elif damage == "invalid_status":
        row["ocr_status"] = "unknown"
    elif damage == "bad_list":
        row["text_blocks"] = {}
    elif damage == "bad_dimensions":
        row["image_width"] = "not a number"
    elif damage == "bad_matrix":
        row["mupdf_to_image_matrix"] = []
    elif damage == "escape":
        row["page_image_path"] = "../outside.png"
    elif damage == "nonfinite":
        row["text_blocks"] = [{"confidence": float("nan")}]
    else:
        row["pdf_page"] = None
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    before = snapshot(directory)
    with pytest.raises(ValueError, match="Existing index is damaged.*record 2"):
        prepare_pdf(sample_pdf, output, pages="1")
    assert snapshot(directory) == before


@pytest.mark.parametrize("field,value", [("schema_version", "unsupported"),
                                          ("expected_pages", [1, "2"]),
                                          ("created_at", None)])
def test_invalid_manifest_rejected_before_writes(sample_pdf, tmp_path, field, value):
    output = tmp_path / "out"
    directory = prepare_pdf(sample_pdf, output, pages="1-2")
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest[field] = value
    path.write_text(json.dumps(manifest))
    before = snapshot(directory)
    with pytest.raises(ValueError, match=field):
        prepare_pdf(sample_pdf, output, pages="1")
    assert snapshot(directory) == before


def test_interruption_between_index_and_manifest_is_recoverable(sample_pdf, tmp_path, monkeypatch):
    import multimodal_pdf_qa.prepare as module

    original = module.write_json
    def interrupted(path, value):
        if path.name == "manifest.json" and value["last_run"]["rendered"] == 1:
            raise KeyboardInterrupt
        original(path, value)

    output = tmp_path / "out"
    monkeypatch.setattr(module, "write_json", interrupted)
    with pytest.raises(KeyboardInterrupt):
        prepare_pdf(sample_pdf, output, pages="1-2")
    directory = next(output.iterdir())
    audit = audit_document(directory)
    assert not audit["ok"] and not audit["render_complete"]
    assert audit["pending_render_pages"] == [2]
    assert audit["issues"] == []
    assert any("successful_render_count: recorded=0, index=1" in item for item in audit["warnings"])
    monkeypatch.setattr(module, "write_json", original)
    prepare_pdf(sample_pdf, output, pages="1-2")
    assert audit_document(directory)["ok"]
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["last_run"]["skipped"] == 1
    assert manifest["successful_render_count"] == 2
    assert manifest["pending_render_count"] == 0
