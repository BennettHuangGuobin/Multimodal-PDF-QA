import json
from pathlib import Path

import pymupdf
import pytest

from multimodal_pdf_qa.audit import audit_document
from multimodal_pdf_qa.pdf import discover_pdfs, inspect_pdf, select_pages, transform_bbox
from multimodal_pdf_qa.prepare import prepare_pdf
from multimodal_pdf_qa.storage import read_jsonl


def records(directory):
    rows, errors, count = read_jsonl(directory / "pages.jsonl")
    assert not errors
    assert count == len(rows)
    return rows


def test_inspect_metadata_geometry(sample_pdf):
    result = inspect_pdf(sample_pdf)
    assert result["page_count"] == 4
    assert result["metadata"]["title"] == "Synthetic QA report"
    assert [p["pdf_page"] for p in result["pages"]] == [1, 2, 3, 4]
    assert result["pages"][0]["page_width"] == 240
    assert result["pages"][1]["rotation"] == 90
    assert result["pages"][1]["page_width"] == 320
    assert result["pages"][2]["crop_box"] == [20, 30, 220, 290]
    assert result["pages"][2]["page_width"] == 200


def test_prepare_schema_audit_and_resume(sample_pdf, tmp_path):
    original = sample_pdf.read_bytes()
    messages = []
    output = tmp_path / "nested" / "processed"
    directory = prepare_pdf(sample_pdf, output, pages="1-3", progress=messages.append)
    rows = records(directory)
    assert len(rows) == 3
    assert len(messages) == 3
    for row in rows:
        assert row["printed_page"] is None
        assert row["render_status"] == "success"
        assert row["ocr_status"] == row["layout_status"] == "not_run"
        assert row["text_blocks"] == row["image_regions"] == row["relations"] == row["errors"] == []
        assert row["bbox_coordinate_system"] == "image_pixels_top_left_xyxy"
        assert row["image_width"] == row["page_width"] * 2
        assert row["image_height"] == row["page_height"] * 2
        assert (directory / row["page_image_path"]).is_file()
    mtimes = [(directory / r["page_image_path"]).stat().st_mtime_ns for r in rows]
    prepare_pdf(sample_pdf, output, pages="1-3")
    assert records(directory) == rows
    assert mtimes == [(directory / r["page_image_path"]).stat().st_mtime_ns for r in rows]
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["last_run"] == {"requested_pages": [1, 2, 3], "render_dpi": 144,
                                    "rendered": 0, "skipped": 3, "failed": 0}
    report = audit_document(directory)
    assert report["ok"] and report["source_verified"]
    assert report["actual_pdf_page_count"] == 4
    assert report["expected_page_count"] == report["successful_render_count"] == 3
    assert report["failed_render_count"] == 0
    assert report["ocr_not_run_pages"] == report["layout_not_run_pages"] == [1, 2, 3]
    assert sample_pdf.read_bytes() == original


def test_page_range_union_dpi_change_and_printed_mapping(sample_pdf, tmp_path):
    output = tmp_path / "processed"
    mapping = tmp_path / "map.json"
    mapping.write_text('{"1": null, "2": "i", "3": 1}')
    directory = prepare_pdf(sample_pdf, output, pages="1-2", printed_pages=mapping)
    prepare_pdf(sample_pdf, output, pages="2-4", dpi=72, printed_pages=mapping)
    rows = records(directory)
    assert [r["pdf_page"] for r in rows] == [1, 2, 3, 4]
    assert [r["printed_page"] for r in rows] == [None, "i", 1, None]
    assert [r["render_dpi"] for r in rows] == [144, 72, 72, 72]
    assert audit_document(directory)["ok"]
    assert json.loads((directory / "manifest.json").read_text())["expected_pages"] == [1, 2, 3, 4]


@pytest.mark.parametrize("spec", ["0", "-1", "2-1", "1-5", "", "abc", "1,", "1-2-3"])
def test_invalid_page_specs(spec):
    with pytest.raises(ValueError, match="1-based"):
        select_pages(spec, 4)


def test_page_selection():
    assert select_pages("3,1-3, 4", 4) == [1, 2, 3, 4]
    assert select_pages(None, 3) == [1, 2, 3]


def test_discovery_and_identity(sample_pdf, tmp_path):
    renamed = tmp_path / "renamed.PDF"
    renamed.write_bytes(sample_pdf.read_bytes())
    assert discover_pdfs(tmp_path) == sorted([sample_pdf, renamed])
    assert inspect_pdf(sample_pdf)["document_id"] == inspect_pdf(renamed)["document_id"]
    another = tmp_path / "other" / "sample.pdf"
    another.parent.mkdir()
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(another)
    assert inspect_pdf(another)["document_id"] != inspect_pdf(sample_pdf)["document_id"]
    with pytest.raises(ValueError, match="No PDF"):
        discover_pdfs(tmp_path / "missing")


@pytest.mark.parametrize("damage", ["missing", "broken", "different_valid_png"])
def test_png_audit_and_repair(sample_pdf, tmp_path, damage):
    output = tmp_path / "processed"
    directory = prepare_pdf(sample_pdf, output, pages="1-2")
    path = directory / "pages/page_0001.png"
    if damage == "missing":
        path.unlink()
    elif damage == "broken":
        path.write_bytes(b"broken png")
    else:
        with pymupdf.open() as doc:
            page = doc.new_page(width=240, height=320)
            page.get_pixmap(dpi=144).save(path)
    report = audit_document(directory)
    assert not report["ok"]
    assert report["image_errors"][0]["pdf_page"] == 1
    assert report["successful_render_count"] == 1
    prepare_pdf(sample_pdf, output, pages="1-2")
    assert audit_document(directory)["ok"]
    run = json.loads((directory / "manifest.json").read_text())["last_run"]
    assert run["rendered"] == run["skipped"] == 1


def test_audit_missing_duplicate_and_malformed_rows(sample_pdf, tmp_path):
    directory = prepare_pdf(sample_pdf, tmp_path / "processed", pages="1-3")
    path = directory / "pages.jsonl"
    lines = path.read_text().splitlines()
    path.write_text(lines[0] + "\n" + lines[0] + "\n" + "{bad\n")
    report = audit_document(directory)
    assert not report["ok"]
    assert report["duplicate_pages"] == [1]
    assert report["missing_pages"] == [2, 3]
    assert report["jsonl_line_count"] == 3
    assert report["jsonl_record_count"] == 2
    assert report["jsonl_errors"]
    before = path.read_bytes()
    with pytest.raises(ValueError, match="damaged"):
        prepare_pdf(sample_pdf, tmp_path / "processed", pages="1-3")
    assert path.read_bytes() == before


@pytest.mark.parametrize("mutation", ["zero", "unexpected", "missing_field", "invalid_status", "escape"])
def test_audit_bad_records(sample_pdf, tmp_path, mutation):
    directory = prepare_pdf(sample_pdf, tmp_path / "processed", pages="1")
    row = records(directory)[0]
    if mutation == "zero":
        row["pdf_page"] = 0
    elif mutation == "unexpected":
        row["pdf_page"] = 5
    elif mutation == "missing_field":
        del row["text_blocks"]
    elif mutation == "invalid_status":
        row["ocr_status"] = "unknown"
    else:
        row["page_image_path"] = "../outside.png"
    (directory / "pages.jsonl").write_text(json.dumps(row) + "\n")
    assert not audit_document(directory)["ok"]


def test_render_failure_then_retry(sample_pdf, tmp_path, monkeypatch):
    import multimodal_pdf_qa.prepare as module

    real = module.render_page

    def flaky(page, target, dpi):
        if page.number == 1:
            raise RuntimeError("injected render failure")
        return real(page, target, dpi)

    monkeypatch.setattr(module, "render_page", flaky)
    output = tmp_path / "processed"
    directory = prepare_pdf(sample_pdf, output, pages="1-3")
    rows = records(directory)
    assert [r["render_status"] for r in rows] == ["success", "failed", "success"]
    assert rows[1]["errors"][0]["message"] == "injected render failure"
    assert audit_document(directory)["failed_render_count"] == 1
    monkeypatch.setattr(module, "render_page", real)
    prepare_pdf(sample_pdf, output, pages="1-3")
    assert audit_document(directory)["ok"]
    assert records(directory)[1]["errors"] == []


def test_interrupt_and_resume(sample_pdf, tmp_path, monkeypatch):
    import multimodal_pdf_qa.prepare as module

    real = module.render_page

    def interrupted(page, target, dpi):
        if page.number == 1:
            raise KeyboardInterrupt
        return real(page, target, dpi)

    monkeypatch.setattr(module, "render_page", interrupted)
    output = tmp_path / "processed"
    with pytest.raises(KeyboardInterrupt):
        prepare_pdf(sample_pdf, output, pages="1-3")
    directory = next(output.iterdir())
    assert [r["render_status"] for r in records(directory)] == ["success", "not_run", "not_run"]
    assert audit_document(directory)["pending_render_pages"] == [2, 3]
    monkeypatch.setattr(module, "render_page", real)
    prepare_pdf(sample_pdf, output, pages="1-3")
    assert audit_document(directory)["ok"]


def test_source_relocation_and_mismatch(sample_pdf, tmp_path):
    directory = prepare_pdf(sample_pdf, tmp_path / "processed", pages="1")
    moved = tmp_path / "moved.pdf"
    sample_pdf.rename(moved)
    report = audit_document(directory)
    assert report["ok"] and not report["source_verified"]
    assert report["actual_pdf_page_count"] is None and report["warnings"]
    assert audit_document(directory, moved)["source_verified"]
    assert not audit_document(directory, sample_pdf)["ok"]
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(sample_pdf)
    report = audit_document(directory, sample_pdf)
    assert not report["ok"]
    assert report["actual_pdf_page_count"] == 1
    assert "Source PDF SHA-256 mismatch" in report["issues"]


def test_transforms_match_visible_colored_region(sample_pdf, tmp_path):
    directory = prepare_pdf(sample_pdf, tmp_path / "processed", dpi=144)
    with pymupdf.open(sample_pdf) as doc:
        for row in records(directory):
            page = doc[row["pdf_page"] - 1]
            # Test against a real drawn shape and image pixels, including rotated CropBoxes.
            shape = page.get_drawings()[0]["rect"]
            box = transform_bbox(list(shape), row["mupdf_to_image_matrix"])
            pix = pymupdf.Pixmap(directory / row["page_image_path"])
            center = (int((box[0] + box[2]) / 2), int((box[1] + box[3]) / 2))
            assert pix.pixel(*center) == (255, 0, 0)
            inverse = list(~pymupdf.Matrix(*row["mupdf_to_image_matrix"]))
            assert transform_bbox(box, inverse) == pytest.approx(list(shape))
            # The fixture draws [40,90,120,180] on a 320-point-high page
            # before cropping: independent PDF y-up coordinates, not a matrix round trip.
            pdf_box = [40, 140, 120, 230]
            assert transform_bbox(pdf_box, row["pdf_to_mupdf_matrix"]) == pytest.approx(list(shape))


def test_force_resets_downstream_status(sample_pdf, tmp_path):
    from multimodal_pdf_qa.runner import run_parser
    from mock_adapters import SyntheticAdapter

    output = tmp_path / "processed"
    directory = prepare_pdf(sample_pdf, output, pages="1")
    run_parser(directory, SyntheticAdapter())
    prepare_pdf(sample_pdf, output, pages="1")
    assert records(directory)[0]["ocr_status"] == "success"
    prepare_pdf(sample_pdf, output, pages="1", force=True)
    assert records(directory)[0]["ocr_status"] == "not_run"


def test_invalid_mapping_and_dpi(sample_pdf, tmp_path):
    with pytest.raises(ValueError, match="DPI"):
        prepare_pdf(sample_pdf, tmp_path / "processed", dpi=0)
    mapping = tmp_path / "map.json"
    mapping.write_text('{"0": "cover"}')
    with pytest.raises(ValueError, match="mapping"):
        prepare_pdf(sample_pdf, tmp_path / "processed", printed_pages=mapping)


def test_encrypted_pdf_rejected(tmp_path):
    path = tmp_path / "encrypted.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    with pytest.raises(ValueError, match="unencrypted"):
        inspect_pdf(path)
