"""Stdlib argparse CLI. JSON to stdout; progress/errors to stderr."""

import argparse
import json
from pathlib import Path
import sys
import tomllib

from .audit import audit_document
from .pdf import PageRangeError, discover_pdfs, inspect_pdf
from .prepare import prepare_pdf


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OCR-first PDF preparation (no model inference)")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Read PDF metadata and page geometry")
    inspect.add_argument("input", type=Path, help="PDF file or non-recursive directory")
    prepare = commands.add_parser("prepare", help="Render pages and build index")
    prepare.add_argument("input", type=Path)
    prepare.add_argument("--output", type=Path)
    prepare.add_argument("--dpi", type=int)
    prepare.add_argument("--pages", help="1-based inclusive pages, e.g. 1-3 or 1,3-5; default all")
    prepare.add_argument("--printed-pages", type=Path, help="JSON page map; single PDF only")
    prepare.add_argument("--config", type=Path, help="TOML with [prepare] defaults")
    prepare.add_argument("--force", action="store_true", help="Rerender requested pages")
    audit = commands.add_parser("audit", help="Check one document directory or a processed root")
    audit.add_argument("input", type=Path)
    audit.add_argument("--pdf", type=Path, help="Relocated source PDF; single document only")
    return parser


def prepare_options(args: argparse.Namespace) -> dict:
    config = {}
    if args.config:
        with args.config.open("rb") as stream:
            config = tomllib.load(stream).get("prepare", {})
        if not isinstance(config, dict) or set(config) - {"dpi", "output", "pages"}:
            raise ValueError("[prepare] supports only dpi, output, pages")
    dpi = args.dpi if args.dpi is not None else config.get("dpi", 144)
    pages = args.pages if args.pages is not None else config.get("pages")
    if type(dpi) is not int or dpi <= 0:
        raise ValueError("DPI must be a positive integer")
    if pages is not None and not isinstance(pages, str):
        raise ValueError("pages must be a string such as '1-3'")
    return {"dpi": dpi, "pages": pages,
            "output": args.output or Path(config.get("output", "data/processed")),
            "printed_pages": args.printed_pages, "force": args.force,
            "progress": lambda message: print(message, file=sys.stderr)}


def discover_document_dirs(source: Path) -> list[Path]:
    """Find direct children with document artifacts, even without a manifest."""
    def candidate(path: Path) -> bool:
        return path.is_dir() and ((path / "manifest.json").exists()
                                  or (path / "pages.jsonl").exists()
                                  or (path / "pages").is_dir())

    if candidate(source):
        return [source]
    if source.is_dir():
        directories = sorted(path for path in source.iterdir() if candidate(path))
        if directories:
            return directories
    raise ValueError(f"No document artifacts found under {source}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    results = []
    exit_code = 0
    try:
        if args.command == "audit":
            directories = discover_document_dirs(args.input)
            if args.pdf and len(directories) != 1:
                raise ValueError("--pdf requires a single document directory")
            for directory in directories:
                try:
                    if not (directory / "manifest.json").is_file():
                        raise ValueError("Missing manifest.json in candidate document directory")
                    report = audit_document(directory, args.pdf)
                    results.append(report)
                    exit_code = max(exit_code, 0 if report["ok"] else 1)
                except Exception as exc:
                    print(f"{directory}: {type(exc).__name__}: {exc}", file=sys.stderr)
                    results.append({"document_dir": str(directory), "ok": False, "error": str(exc)})
                    exit_code = 1
        else:
            pdfs = discover_pdfs(args.input)
            options = prepare_options(args) if args.command == "prepare" else {}
            if options.get("printed_pages") and len(pdfs) != 1:
                raise ValueError("--printed-pages requires a single PDF")
            for pdf in pdfs:
                try:
                    if args.command == "inspect":
                        results.append(inspect_pdf(pdf))
                    else:
                        directory = prepare_pdf(pdf, **options)
                        report = json.loads((directory / "audit.json").read_text(encoding="utf-8"))
                        results.append({"source_pdf": str(pdf), "document_dir": str(directory),
                                        "ok": report["ok"],
                                        "successful_render_count": report["successful_render_count"]})
                        exit_code = max(exit_code, 0 if report["ok"] else 1)
                except PageRangeError as exc:
                    print(f"{pdf}: {exc}", file=sys.stderr)
                    results.append({"source_pdf": str(pdf), "error": str(exc)})
                    exit_code = 2
                except Exception as exc:
                    print(f"{pdf}: {type(exc).__name__}: {exc}", file=sys.stderr)
                    results.append({"source_pdf": str(pdf), "error": str(exc)})
                    exit_code = max(exit_code, 1)
    except (OSError, ValueError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; completed page checkpoints are saved. Rerun to resume.", file=sys.stderr)
        return 130
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return exit_code
