"""Sequential, resumable page rendering with atomic per-page checkpoints."""

from collections.abc import Callable
import json
from pathlib import Path

import pymupdf

from .audit import audit_document, check_image, manifest_scope, record_errors, render_counts, utc_now
from .models import PageRecord, ProcessingError
from .pdf import open_pdf, page_geometry, select_pages
from .storage import image_path, read_jsonl, sha256_file, write_json, write_jsonl


def load_printed_pages(path: Path | None, page_count: int) -> dict[int, str | int | None]:
    if path is None:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Printed-page map must be an object keyed by PDF page number")
    result = {}
    for key, value in raw.items():
        if (not key.isdigit() or str(int(key)) != key or not 1 <= int(key) <= page_count
                or (value is not None and type(value) not in (int, str))):
            raise ValueError(f"Invalid printed-page mapping: {key!r}: {value!r}")
        result[int(key)] = value
    return result


def render_page(page: pymupdf.Page, target: Path, dpi: int) -> tuple[int, int]:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False, annots=True)
    temporary = target.with_name(f".{target.stem}.tmp.png")
    try:
        pix.save(temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return pix.width, pix.height


def prepare_pdf(
    source: Path,
    output: Path,
    *,
    dpi: int = 144,
    pages: str | None = None,
    printed_pages: Path | None = None,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
) -> Path:
    if type(dpi) is not int or dpi <= 0:
        raise ValueError("DPI must be a positive integer")
    source = Path(source).resolve()
    digest = sha256_file(source)
    document_id = f"doc_{digest[:16]}"
    directory = Path(output).resolve() / document_id
    with open_pdf(source) as doc:
        selected = select_pages(pages, doc.page_count)
        printed = load_printed_pages(printed_pages, doc.page_count)
        manifest_path = directory / "manifest.json"
        index_path = directory / "pages.jsonl"
        records = {}
        historical_pages = set()
        expected = set(selected)
        created_at = utc_now()
        if manifest_path.exists():
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            historical_pages = manifest_scope(previous)
            if previous.get("schema_version") != "1.0":
                raise ValueError("Existing manifest has unsupported schema_version")
            if previous.get("status") not in ("running", "incomplete", "complete"):
                raise ValueError("Existing manifest has invalid status")
            if not isinstance(previous.get("created_at"), str):
                raise ValueError("Existing manifest has invalid created_at")
            if (previous.get("source_sha256") != digest
                    or previous.get("source_page_count") != doc.page_count
                    or previous.get("document_id") != document_id):
                raise ValueError("Existing manifest does not match this source PDF")
            expected.update(historical_pages)
            created_at = previous["created_at"]
        elif index_path.exists():
            raise ValueError("Index exists without manifest; restore manifest or use another output root")
        if index_path.exists():
            rows, errors, _ = read_jsonl(index_path)
            for position, row in enumerate(rows, 1):
                number = row.get("pdf_page")
                problems = record_errors(row, document_id)
                if type(number) is not int or number not in historical_pages or number in records:
                    problems.append("invalid, unexpected or duplicate pdf_page")
                try:
                    # This validates the path without requiring the PNG to
                    # exist: missing/corrupt images remain safely recoverable.
                    image_path(directory, row.get("page_image_path", ""))
                    json.dumps(row, allow_nan=False)
                except (ValueError, TypeError) as exc:
                    problems.append(str(exc))
                if problems:
                    errors.append(f"record {position}, PDF page {number!r}: {'; '.join(problems)}")
                if type(number) is int:
                    records[number] = row
            if errors:
                raise ValueError(f"Existing index is damaged; audit it before repair: {errors}")
        (directory / "pages").mkdir(parents=True, exist_ok=True)
        for number in expected:
            if number not in records:
                records[number] = PageRecord(
                    document_id=document_id, pdf_page=number, render_dpi=dpi,
                    page_image_path=f"pages/page_{number:04d}.png",
                    printed_page=printed.get(number),
                ).to_dict()
        for number, printed_page in printed.items():
            if number in records:
                records[number]["printed_page"] = printed_page
        manifest = {
            "schema_version": "1.0", "document_id": document_id,
            "source_pdf": str(source), "source_sha256": digest,
            "source_page_count": doc.page_count, "metadata": doc.metadata,
            "expected_pages": sorted(expected), "expected_page_count": len(expected),
            "created_at": created_at, "updated_at": utc_now(), "status": "running",
            "last_run": {"requested_pages": selected, "render_dpi": dpi,
                         "rendered": 0, "skipped": 0, "failed": 0},
            "renderer": {"name": "PyMuPDF", "version": pymupdf.VersionBind,
                         "colorspace": "RGB", "alpha": False, "annotations": True},
        }
        # Manifest first: if interrupted, audit will expose any missing records.
        manifest.update(render_counts(records.values()))
        write_json(manifest_path, manifest)
        write_jsonl(index_path, records)
        for position, number in enumerate(selected, 1):
            previous = records[number]
            # Recompute metadata before reuse so phase-1 coordinate matrices
            # cannot survive a rerun just because their PNG is intact.
            geometry, geometry_error = {}, None
            try:
                page = doc.load_page(number - 1)
                geometry = page_geometry(page, dpi)
            except Exception as exc:
                geometry_error = exc
            reusable = (geometry_error is None and not force
                        and previous.get("render_status") == "success"
                        and previous.get("render_dpi") == dpi
                        and previous.get("page_image_path") == f"pages/page_{number:04d}.png"
                        and all(previous.get(key) == value for key, value in geometry.items())
                        and check_image(directory, previous) is None)
            if reusable:
                manifest["last_run"]["skipped"] += 1
                action = "skipped"
            else:
                record = PageRecord(
                    document_id=document_id, pdf_page=number, render_dpi=dpi,
                    page_image_path=f"pages/page_{number:04d}.png",
                    printed_page=previous.get("printed_page"),
                )
                try:
                    if geometry_error is not None:
                        raise geometry_error
                    for key, value in geometry.items():
                        setattr(record, key, value)
                    target = directory / record.page_image_path
                    record.image_width, record.image_height = render_page(page, target, dpi)
                    record.page_image_sha256 = sha256_file(target)
                    record.render_status = "success"
                    action = "rendered"
                except Exception as exc:
                    record.render_status = "failed"
                    record.errors.append(ProcessingError("render", str(exc), type(exc).__name__))
                    action = "failed"
                records[number] = record.to_dict()
                manifest["last_run"][action] += 1
                write_jsonl(index_path, records)
            manifest["updated_at"] = utc_now()
            manifest.update(render_counts(records.values()))
            write_json(manifest_path, manifest)
            if progress:
                detail = f" {records[number]['errors']}" if action == "failed" else ""
                progress(f"{source.name} [{position}/{len(selected)}] PDF page {number}: {action}{detail}")
        manifest["status"] = "complete" if all(
            row["render_status"] == "success" for row in records.values()
        ) else "incomplete"
        manifest.update(render_counts(records.values()))
        write_json(manifest_path, manifest)
    report = audit_document(directory)
    if not report["render_complete"]:
        manifest["status"] = "incomplete"
        write_json(manifest_path, manifest)
        audit_document(directory)
    return directory
