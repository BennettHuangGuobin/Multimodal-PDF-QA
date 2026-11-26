"""PDF inspection and coordinate metadata, deliberately without text extraction."""

from pathlib import Path

import pymupdf

from .storage import sha256_file


def discover_pdfs(source: Path) -> list[Path]:
    source = Path(source)
    if source.is_file() and source.suffix.lower() == ".pdf":
        return [source.resolve()]
    if source.is_dir():
        files = sorted(p.resolve() for p in source.iterdir()
                       if p.is_file() and p.suffix.lower() == ".pdf")
        if files:
            return files
    raise ValueError(f"No PDF found: {source} (directories are scanned non-recursively)")


def open_pdf(path: Path) -> pymupdf.Document:
    doc = pymupdf.open(path)
    if not doc.is_pdf or doc.needs_pass or doc.page_count == 0:
        doc.close()
        raise ValueError(f"Expected a nonempty, unencrypted PDF: {path}")
    return doc


def page_geometry(page: pymupdf.Page, dpi: int) -> dict:
    # get_pixmap renders the rotated CropBox. Coordinates from PyMuPDF APIs
    # are unrotated points; apply rotation, scale, then pixmap-origin shift.
    matrix = page.rotation_matrix * pymupdf.Matrix(dpi / 72, dpi / 72)
    device_rect = (page.rect * pymupdf.Matrix(dpi / 72, dpi / 72)).irect
    matrix = matrix * pymupdf.Matrix(1, 0, 0, 1, -device_rect.x0, -device_rect.y0)
    return {
        "page_width": page.rect.width,
        "page_height": page.rect.height,
        "rotation": page.rotation,
        "media_box": list(page.mediabox),
        "crop_box": list(page.cropbox),
        "pdf_to_mupdf_matrix": list(page.transformation_matrix),
        "mupdf_to_image_matrix": list(matrix),
    }


def transform_bbox(bbox: list[float], matrix: list[float]) -> list[float]:
    """Transform an axis-aligned box using a stored six-element affine matrix."""
    return list(pymupdf.Rect(bbox) * pymupdf.Matrix(*matrix))


def inspect_pdf(path: Path) -> dict:
    path = Path(path).resolve()
    digest = sha256_file(path)
    with open_pdf(path) as doc:
        return {
            "document_id": f"doc_{digest[:16]}",
            "source_pdf": str(path),
            "source_sha256": digest,
            "page_count": doc.page_count,
            "metadata": doc.metadata,
            "pages": [dict(pdf_page=i + 1, **page_geometry(doc.load_page(i), 72))
                      for i in range(doc.page_count)],
        }


def select_pages(spec: str | None, page_count: int) -> list[int]:
    if spec is None:
        return list(range(1, page_count + 1))
    pages = set()
    try:
        for item in spec.split(","):
            ends = [int(part.strip()) for part in item.split("-")]
            if len(ends) not in (1, 2):
                raise ValueError
            start, stop = ends[0], ends[-1]
            if start < 1 or stop < start or stop > page_count:
                raise ValueError
            pages.update(range(start, stop + 1))
    except ValueError:
        raise ValueError(f"Invalid pages {spec!r}; use 1-based ranges within 1-{page_count}") from None
    return sorted(pages)
