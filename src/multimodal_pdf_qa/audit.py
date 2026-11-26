"""Read-only inspection of inputs/index/images, plus an atomic audit report."""

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import pymupdf

from .models import PageRecord
from .pdf import open_pdf
from .storage import image_path, read_jsonl, sha256_file, write_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_image(document_dir: Path, record: dict) -> str | None:
    """Decode the actual PNG and verify its dimensions and content fingerprint."""
    try:
        path = image_path(document_dir, record["page_image_path"])
        with path.open("rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                raise ValueError("not a PNG")
        pix = pymupdf.Pixmap(path)
        if (pix.width, pix.height) != (record["image_width"], record["image_height"]):
            raise ValueError("PNG dimensions disagree with index")
        del pix
        for dimension in ("width", "height"):
            expected = record[f"page_{dimension}"] * record["render_dpi"] / 72
            if abs(record[f"image_{dimension}"] - expected) > 1.01:
                raise ValueError("image dimensions disagree with page size / DPI")
        if sha256_file(path) != record["page_image_sha256"]:
            raise ValueError("PNG SHA-256 mismatch")
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def record_errors(record: dict, document_id: str) -> list[str]:
    errors = []
    missing = set(PageRecord.__dataclass_fields__) - record.keys()
    if missing:
        errors.append(f"missing fields: {', '.join(sorted(missing))}")
    if record.get("document_id") != document_id:
        errors.append("document_id mismatch")
    if record.get("schema_version") != "1.0":
        errors.append("unsupported schema_version")
    if record.get("bbox_coordinate_system") != "image_pixels_top_left_xyxy":
        errors.append("invalid bbox coordinate system")
    printed = record.get("printed_page")
    if printed is not None and type(printed) not in (str, int):
        errors.append("invalid printed_page")
    if type(record.get("render_dpi")) is not int or record["render_dpi"] <= 0:
        errors.append("invalid render_dpi")
    if not isinstance(record.get("page_image_path"), str):
        errors.append("invalid page_image_path")
    for field in ("text_blocks", "image_regions", "relations", "errors"):
        if not isinstance(record.get(field), list):
            errors.append(f"{field} must be a list")
    for stage in ("render", "ocr", "layout"):
        if record.get(f"{stage}_status") not in ("not_run", "success", "failed"):
            errors.append(f"invalid {stage}_status")
    if record.get("render_status") == "success":
        for field in ("page_width", "page_height", "image_width", "image_height"):
            value = record.get(field)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                errors.append(f"invalid {field}")
        if record.get("rotation") not in (0, 90, 180, 270):
            errors.append("invalid rotation")
        for field, size in (("media_box", 4), ("crop_box", 4),
                            ("pdf_to_mupdf_matrix", 6), ("mupdf_to_image_matrix", 6)):
            value = record.get(field)
            if (not isinstance(value, list) or len(value) != size
                    or any(type(x) not in (int, float) or not math.isfinite(x) for x in value)):
                errors.append(f"invalid {field}")
    return errors


def audit_document(document_dir: Path, pdf_path: Path | None = None) -> dict:
    document_dir = Path(document_dir).resolve()
    manifest = json.loads((document_dir / "manifest.json").read_text(encoding="utf-8"))
    expected_list = manifest.get("expected_pages")
    page_count = manifest.get("source_page_count")
    if (type(page_count) is not int or page_count < 1
            or not isinstance(expected_list, list) or not expected_list
            or any(type(n) is not int or not 1 <= n <= page_count for n in expected_list)
            or len(set(expected_list)) != len(expected_list)):
        raise ValueError("Invalid manifest page count / expected_pages")
    expected = set(expected_list)
    issues, warnings = [], []
    actual_page_count = None
    source_verified = False
    source = Path(pdf_path) if pdf_path is not None else Path(manifest["source_pdf"])
    if not source.is_file():
        message = f"Source PDF unavailable: {source}"
        (issues if pdf_path is not None else warnings).append(message)
    else:
        try:
            with open_pdf(source) as doc:
                actual_page_count = doc.page_count
            source_verified = sha256_file(source) == manifest["source_sha256"]
            if not source_verified:
                issues.append("Source PDF SHA-256 mismatch")
            if actual_page_count != page_count:
                issues.append("Actual PDF page count differs from manifest")
        except Exception as exc:
            issues.append(f"Cannot inspect source PDF: {exc}")
    try:
        rows, jsonl_errors, line_count = read_jsonl(document_dir / "pages.jsonl")
    except (OSError, UnicodeError) as exc:
        rows, jsonl_errors, line_count = [], [str(exc)], 0
    numbered = [r for r in rows if type(r.get("pdf_page")) is int and r["pdf_page"] >= 1]
    invalid_page_rows = len(rows) - len(numbered)
    counts = Counter(r["pdf_page"] for r in numbered)
    duplicates = sorted(n for n, count in counts.items() if count > 1)
    missing = sorted(expected - counts.keys())
    unexpected = sorted(counts.keys() - expected)
    invalid_records, image_errors = [], []
    successful, failed, pending = set(), set(), set()
    ocr_pending, layout_pending, ocr_failed, layout_failed = set(), set(), set(), set()
    for row in numbered:
        page = row["pdf_page"]
        problems = record_errors(row, manifest["document_id"])
        if problems:
            invalid_records.append({"pdf_page": page, "errors": problems})
        status = row.get("render_status")
        if status == "success":
            error = check_image(document_dir, row)
            if error:
                image_errors.append({"pdf_page": page, "error": error})
            elif page in expected and not problems:
                successful.add(page)
        elif status == "failed":
            failed.add(page)
        elif status == "not_run":
            pending.add(page)
        for stage, not_run, failures in (("ocr", ocr_pending, ocr_failed),
                                         ("layout", layout_pending, layout_failed)):
            if row.get(f"{stage}_status") == "not_run":
                not_run.add(page)
            elif row.get(f"{stage}_status") == "failed":
                failures.add(page)
    counts_ok = line_count == len(expected) and len(rows) == len(expected)
    ok = not any((issues, jsonl_errors, invalid_page_rows, duplicates, missing, unexpected,
                  invalid_records, image_errors, failed, pending, ocr_failed, layout_failed))
    ok = ok and counts_ok and successful == expected
    report = {
        "schema_version": "1.0", "document_id": manifest["document_id"],
        "checked_at": utc_now(), "ok": ok,
        "source_verified": source_verified,
        "actual_pdf_page_count": actual_page_count,
        "manifest_pdf_page_count": page_count,
        "expected_page_count": len(expected), "expected_pages": sorted(expected),
        "successful_render_count": len(successful),
        "failed_render_count": len(failed & expected),
        "pending_render_pages": sorted(pending),
        "unusable_render_pages": sorted(expected - successful),
        "missing_pages": missing, "duplicate_pages": duplicates,
        "unexpected_pages": unexpected, "invalid_page_record_count": invalid_page_rows,
        "jsonl_record_count": len(rows), "jsonl_line_count": line_count,
        "record_count_matches": counts_ok, "jsonl_errors": jsonl_errors,
        "invalid_records": invalid_records, "image_errors": image_errors,
        "ocr_not_run_pages": sorted(ocr_pending), "layout_not_run_pages": sorted(layout_pending),
        "ocr_failed_pages": sorted(ocr_failed), "layout_failed_pages": sorted(layout_failed),
        "issues": issues, "warnings": warnings,
    }
    write_json(document_dir / "audit.json", report)
    return report
