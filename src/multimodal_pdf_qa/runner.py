"""Single-writer adapter execution over prepared PNGs; no model implementations."""

from copy import deepcopy
import json
from pathlib import Path

from .adapters import DocumentParserAdapter
from .audit import audit_document, check_image, manifest_scope, record_errors
from .models import PageRecord, ProcessingError
from .pdf import select_pages
from .storage import image_path, read_jsonl, write_jsonl
from .validation import (stage_has_data, validate_adapter, validate_page_payload,
                         validate_parser_result)


def run_parser(document_dir: Path, adapter: DocumentParserAdapter, *,
               pages: str | None = None, force: bool = False) -> dict:
    """Validate, execute and checkpoint one page at a time.

    ``adapter.name`` is the cache identity: include backend version/configuration.
    Model/validation errors are persisted per owned stage, preserving previously
    accepted payloads. Storage errors and interrupts propagate; atomic JSONL
    replacement leaves the previous checkpoint readable. Adapters must treat
    input files as read-only. Concurrent writers are not supported.
    """
    stages = validate_adapter(adapter.name, adapter.stages)
    directory = Path(document_dir).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    expected = manifest_scope(manifest)
    if manifest.get("schema_version") != "1.0":
        raise ValueError("Unsupported manifest schema_version")
    selected = sorted(expected) if pages is None else select_pages(pages, manifest["source_page_count"])
    if not set(selected) <= expected:
        raise ValueError("Requested pages have not been prepared")
    rows, errors, _ = read_jsonl(directory / "pages.jsonl")
    records, decoded = {}, {}
    for row in rows:
        problems = record_errors(row, manifest["document_id"])
        number = row.get("pdf_page")
        if type(number) is not int or number not in expected or number in records:
            problems.append("invalid, unexpected or duplicate pdf_page")
        try:
            page = PageRecord.from_dict(row)
            validate_page_payload(page)
            image_path(directory, page.page_image_path)
        except (ValueError, TypeError, AttributeError) as exc:
            problems.append(str(exc))
        if problems:
            errors.append(f"PDF page {number!r}: {'; '.join(problems)}")
        elif type(number) is int:
            records[number], decoded[number] = row, page
    if errors or set(records) != expected:
        raise ValueError(f"Invalid page index; repair with prepare before parsing: {errors}")
    # Existing real v1.0 outputs contain not_run/empty payloads and migrate
    # losslessly. Hand-populated legacy results have no trustworthy provenance:
    # do not silently bind them to a PNG or reinterpret images as generic layout.
    for number in selected:
        page = decoded[number]
        if page.schema_version == "1.0" and (
            any(getattr(page, f"{s}_status") != "not_run" or stage_has_data(page, s)
                for s in ("ocr", "layout")) or page.relations or page.parser_provenance
        ):
            raise ValueError(f"PDF page {number}: legacy parsed results lack verified provenance; "
                             "back up results and force-prepare this page before parsing")
    report = {"document_id": manifest["document_id"], "parsed_pages": [],
              "failed_pages": [], "skipped_pages": []}
    for number in selected:
        page = decoded[number]
        if page.render_status != "success":
            report["skipped_pages"].append({"pdf_page": number, "reason": "render_not_success"})
            continue
        error = check_image(directory, records[number])
        if error:
            raise ValueError(f"PDF page {number}: unusable parser input: {error}; rerun prepare")
        if not force and all(
            getattr(page, f"{s}_status") == "success"
            and s in page.parser_provenance
            and page.parser_provenance[s].adapter == adapter.name
            and page.parser_provenance[s].image_sha256 == page.page_image_sha256 for s in stages
        ):
            report["skipped_pages"].append({"pdf_page": number, "reason": "already_parsed"})
            continue
        try:
            result = adapter.parse(image_path(directory, page.page_image_path), deepcopy(page))
            candidate = validate_parser_result(result, page, adapter_name=adapter.name, stages=stages)
        except Exception as exc:
            candidate = deepcopy(page)
            candidate.schema_version = "1.1"
            candidate.errors = [e for e in candidate.errors if e.stage not in stages]
            for stage in stages:
                setattr(candidate, f"{stage}_status", "failed")
                candidate.errors.append(ProcessingError(
                    stage, f"{adapter.name}: {str(exc) or type(exc).__name__}", type(exc).__name__))
            validate_page_payload(candidate)
        # Catch an input changed by a backend/external writer before committing
        # coordinates against the wrong image. Never relabel such a result valid.
        if check_image(directory, records[number]) is not None:
            raise ValueError(f"PDF page {number}: PNG changed during parsing; no result written")
        next_records = dict(records)
        next_records[number] = candidate.to_dict()
        write_jsonl(directory / "pages.jsonl", next_records)  # outside model-error handler
        records, decoded[number] = next_records, candidate
        key = "failed_pages" if any(getattr(candidate, f"{s}_status") == "failed" for s in stages) else "parsed_pages"
        report[key].append(number)
    report["audit"] = audit_document(directory)
    return report
