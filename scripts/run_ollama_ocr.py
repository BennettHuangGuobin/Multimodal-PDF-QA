"""Run local OCR on explicitly selected prepared pages; private telemetry stays in data/."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

from multimodal_pdf_qa.backends.ollama_ocr import DEFAULT_MODEL, OllamaOCRAdapter
from multimodal_pdf_qa.runner import run_parser
from multimodal_pdf_qa.storage import read_jsonl, write_json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document_dir", type=Path)
    parser.add_argument("--pages", required=True, help="Explicit prepared PDF pages, e.g. 1-3")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=180, help="Socket timeout in seconds, not a server cancellation")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--num-predict", type=int, default=8192)
    parser.add_argument("--force", action="store_true", help="Replace existing OCR results for these pages")
    args = parser.parse_args(argv)
    # This experiment entry point must never persist private responses in a
    # tracked source/docs path. The programmatic adapter is separately reusable.
    directory = args.document_dir.resolve()
    data_root = Path(__file__).resolve().parents[1] / "data"
    if not directory.is_relative_to(data_root.resolve()) or directory == data_root.resolve():
        parser.error("document_dir must be a prepared document under this checkout's ignored data/ directory")
    if not (directory / "manifest.json").is_file():
        parser.error("document_dir must contain manifest.json; prepare/copy a document first")
    if args.num_ctx <= 0 or args.num_predict <= 0:
        parser.error("--num-ctx and --num-predict must be positive")
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    adapter, report, error, code = None, None, None, 1
    try:
        adapter = OllamaOCRAdapter(model=args.model, timeout=args.timeout,
                                   options={"num_ctx": args.num_ctx, "num_predict": args.num_predict})
        report = run_parser(directory, adapter, pages=args.pages, force=args.force)
        code = 0 if report["audit"]["ok"] and not report["failed_pages"] else 1
    except KeyboardInterrupt:
        error, code = "Interrupted; check saved JSONL before retrying", 130
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        attempts = [] if adapter is None else adapter.attempts
        # Record what actually reached the index separately from HTTP success.
        try:
            rows, _, _ = read_jsonl(directory / "pages.jsonl")
            statuses = {r["pdf_page"]: r.get("ocr_status") for r in rows}
        except (OSError, ValueError, KeyError):
            statuses = {}
        for attempt in attempts:
            attempt["persisted_ocr_status"] = statuses.get(attempt["pdf_page"])
        log = {"started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
               "requested_pages": args.pages, "force": args.force,
               "requested_model": args.model, "timeout_seconds": args.timeout,
               "requested_options": {"num_ctx": args.num_ctx, "num_predict": args.num_predict},
               "identity": None if adapter is None else adapter.identity,
               "overall_elapsed_seconds": time.perf_counter() - started,
               "attempts": attempts, "runner_report": report, "error": error}
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
        log_path = directory / "ollama_runs" / f"{run_id}.json"
        write_json(log_path, log)
    # No OCR text on stdout. Complete responses are private in the run log/index.
    print(json.dumps({"log_path": str(log_path), "overall_elapsed_seconds": log["overall_elapsed_seconds"],
                      "attempts": [{k: v for k, v in item.items() if k != "response_text"} for item in attempts],
                      "parsed_pages": None if report is None else report["parsed_pages"],
                      "skipped_pages": None if report is None else report["skipped_pages"],
                      "error": error}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
