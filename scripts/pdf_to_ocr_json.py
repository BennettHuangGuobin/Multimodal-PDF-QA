#!/usr/bin/env python3
"""One-command PDF -> prepared PNGs -> local Ollama OCR -> JSON export.

Place this file at scripts/pdf_to_ocr_json.py inside Multimodal-PDF-QA.
Uses the existing prepare_pdf and run_ollama_ocr.py; does not download models.

Examples:
  .venv/bin/python scripts/pdf_to_ocr_json.py 'data/input/report.pdf' --pages 1-3
  .venv/bin/python scripts/pdf_to_ocr_json.py 'data/input/report.pdf'
  .venv/bin/python scripts/pdf_to_ocr_json.py 'data/input/report.pdf' --pages 4-5 --show-text
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "ocr_pipeline"
DEFAULT_MODEL = "openbmb/minicpm-ocr1.0:f16"


def export_json(document_dir: Path, pdf: Path, selected: list[int], *, model: str) -> tuple[Path, dict]:
    """Export all indexed pages, indicating which pages lack valid OCR output.

    Runner can retain *old* text after a failed forced re-run; never expose that
    stale text as a new successful OCR result.
    """
    from multimodal_pdf_qa.storage import read_jsonl, write_json

    rows, errors, _ = read_jsonl(document_dir / "pages.jsonl")
    if errors:
        raise ValueError("Invalid pages.jsonl: " + "; ".join(errors))

    pages = []
    for row in sorted(rows, key=lambda item: item["pdf_page"]):
        status = row.get("ocr_status", "not_run")
        previous_text = row.get("page_text")
        accepted_text = (previous_text if status == "success" and isinstance(previous_text, dict)
                         else None)
        text = accepted_text.get("text") if accepted_text else None
        if not isinstance(text, str):
            text = None
        provenance = row.get("parser_provenance") or {}
        ocr_provenance = provenance.get("ocr") or {}
        pages.append({
            "pdf_page": row["pdf_page"],
            "printed_page": row.get("printed_page"),
            "page_image": row.get("page_image_path"),  # relative to document_dir
            "image_width": row.get("image_width"),
            "image_height": row.get("image_height"),
            "ocr_status": status,
            "layout_status": row.get("layout_status", "not_run"),
            "character_count": len(text) if text is not None else 0,
            "ocr_source": accepted_text.get("source") if accepted_text else None,
            "text": text,
            "ocr_empty_reason": ocr_provenance.get("empty_reason") if status == "success" else None,
            "stale_text_preserved_in_index": status != "success" and previous_text is not None,
            "errors": [e.get("message") for e in row.get("errors", [])
                       if isinstance(e, dict) and e.get("stage") in ("ocr", "render")],
        })

    count = {"success": 0, "failed": 0, "not_run": 0}
    for page in pages:
        status = page["ocr_status"]
        count[status] = count.get(status, 0) + 1

    data = {
        "document_id": document_dir.name,
        "source_pdf": str(pdf),
        "document_dir": str(document_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "requested_pages": selected,
        "model_requested": model,
        "counts_for_all_prepared_pages": count,
        "pages": pages,
    }
    destination = document_dir / "ocr_results.json"
    write_json(destination, data)  # Existing project helper writes atomically.
    return destination, data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Input one PDF; automatically render PNG pages, run local OCR, and export ocr_results.json."
    )
    parser.add_argument("pdf", type=Path, help="Input PDF file (not a directory)")
    parser.add_argument("--pages", help="1-based page selection, e.g. 1-3 or 1,3-5; default: all pages")
    parser.add_argument("--dpi", type=int, default=144, help="Rendering DPI (default: 144)")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT,
                        help="Output root under this project's ignored data/ directory")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=180, help="Ollama socket timeout per request (seconds)")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--num-predict", type=int, default=8192)
    parser.add_argument("--force-ocr", action="store_true", help="Redo OCR for selected pages")
    parser.add_argument("--show-text", action="store_true", help="Print full OCR text for selected pages")
    args = parser.parse_args(argv)

    pdf = args.pdf.expanduser().resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        parser.error(f"PDF file not found: {pdf}")
    data_root = (PROJECT_ROOT / "data").resolve()
    output_root = args.output_root.expanduser().resolve()
    if output_root == data_root or not output_root.is_relative_to(data_root):
        parser.error("--output-root must be a subdirectory of this project's ignored data/ directory")
    if args.dpi <= 0 or args.num_ctx <= 0 or args.num_predict <= 0:
        parser.error("--dpi, --num-ctx and --num-predict must be positive")

    # Use the same backend and preparation code already tested in the project.
    try:
        from multimodal_pdf_qa.backends.ollama_ocr import OllamaOCRAdapter
        from multimodal_pdf_qa.pdf import select_pages
        from multimodal_pdf_qa.prepare import prepare_pdf

        print(f"[1/3] Checking locally installed Ollama model: {args.model}", flush=True)
        OllamaOCRAdapter(model=args.model, timeout=args.timeout,
                         options={"num_ctx": args.num_ctx, "num_predict": args.num_predict})

        print(f"[2/3] Preparing PDF pages -> {output_root}", flush=True)
        document_dir = prepare_pdf(
            pdf, output_root, dpi=args.dpi, pages=args.pages,
            progress=lambda message: print("  " + message, flush=True),
        )
        manifest = json.loads((document_dir / "manifest.json").read_text(encoding="utf-8"))
        selected = select_pages(args.pages, manifest["source_page_count"])
        print(f"[3/3] Running OCR on {len(selected)} selected page(s).", flush=True)
        if len(selected) > 20:
            print("  Note: This is a long document. OCR executes sequentially and may take substantial time.",
                  flush=True)

        # Preserve the original fifth-round telemetry and resume behavior.
        command = [
            sys.executable, str(PROJECT_ROOT / "scripts" / "run_ollama_ocr.py"),
            str(document_dir), "--pages", args.pages or f"1-{manifest['source_page_count']}",
            "--model", args.model, "--timeout", str(args.timeout),
            "--num-ctx", str(args.num_ctx), "--num-predict", str(args.num_predict),
        ]
        if args.force_ocr:
            command.append("--force")
        process = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True)
        try:
            run_summary = json.loads(process.stdout)
        except json.JSONDecodeError:
            run_summary = {"error": process.stdout.strip() or "OCR runner did not return JSON"}

        destination, data = export_json(document_dir, pdf, selected, model=args.model)
        selected_pages = {page["pdf_page"]: page for page in data["pages"]}
        successful = [p for p in selected if selected_pages.get(p, {}).get("ocr_status") == "success"]
        unsuccessful = [p for p in selected if p not in successful]
        print(f"\nOCR completed: {len(successful)}/{len(selected)} selected pages successful")
        print(f"PNG directory: {document_dir / 'pages'}")
        print(f"Index:         {document_dir / 'pages.jsonl'}")
        print(f"JSON:          {destination}")
        print(f"Audit:         {document_dir / 'audit.json'}")
        if run_summary.get("log_path"):
            print(f"Private log:   {run_summary['log_path']}")
        if run_summary.get("skipped_pages"):
            print(f"Reused OCR:    {len(run_summary['skipped_pages'])} pages")
        if unsuccessful:
            print(f"Unsuccessful selected pages: {unsuccessful}", file=sys.stderr)
        if run_summary.get("error"):
            print(f"OCR error: {run_summary['error']}", file=sys.stderr)
        if process.stderr.strip():
            print("OCR runner stderr:\n" + process.stderr.strip(), file=sys.stderr)
        if args.show_text:
            for number in selected:
                page = selected_pages[number]
                print(f"\n{'=' * 18} PDF page {number} ({page['ocr_status']}) {'=' * 18}")
                print(page["text"] if page["text"] is not None else "(No accepted OCR text)")

        return 0 if process.returncode == 0 and not unsuccessful else 1
    except KeyboardInterrupt:
        print("Interrupted. Saved per-page checkpoints can be reused on the next run.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
