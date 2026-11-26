"""Small atomic JSON/JSONL helpers; no image collection is kept in memory."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path: Path, value: dict) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_jsonl(path: Path, records: dict[int, dict]) -> None:
    atomic_text(path, "".join(
        json.dumps(records[page], ensure_ascii=False, allow_nan=False) + "\n"
        for page in sorted(records)
    ))


def read_jsonl(path: Path) -> tuple[list[dict], list[str], int]:
    records, errors = [], []
    count = 0
    with path.open(encoding="utf-8") as stream:
        for count, line in enumerate(stream, 1):
            try:
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError("record must be a JSON object")
                records.append(item)
            except (ValueError, TypeError) as exc:
                errors.append(f"line {count}: {exc}")
    return records, errors, count


def image_path(document_dir: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute():
        raise ValueError("page_image_path must be relative to the document directory")
    resolved = (document_dir / path).resolve()
    if not resolved.is_relative_to(document_dir.resolve()):
        raise ValueError("page_image_path escapes the document directory")
    return resolved
