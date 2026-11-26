"""PDF inspection and coordinate metadata, deliberately without text extraction."""

import math
from pathlib import Path

import pymupdf

from .storage import sha256_file


class PageRangeError(ValueError):
    """A caller-supplied page selection is invalid for the document."""


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
    # CropBox uses unscaled coordinates with its y-axis relative to MediaBox's
    # top edge. Reconstruct its PDF user-space top-left without changing /Rotate.
    # On rotated pages transformation_matrix can drop this translation.
    media, crop = page.mediabox, page.cropbox
    raw_crop = pymupdf.Rect(crop.x0, media.y1 - crop.y1,
                           crop.x1, media.y1 - crop.y0)
    if raw_crop.is_empty or not media.contains(raw_crop):
        raise ValueError("Unsupported page geometry: CropBox must lie within MediaBox")
    width, height = page.rect.width, page.rect.height
    if page.rotation in (90, 270):
        width, height = height, width
    if not (math.isclose(width, crop.width, rel_tol=1e-6, abs_tol=1e-4)
            and math.isclose(height, crop.height, rel_tol=1e-6, abs_tol=1e-4)):
        # rotation_matrix itself can omit UserUnit scaling; do not emit a
        # plausible but incorrect image-space transform for these documents.
        raise ValueError("Unsupported page geometry: non-default UserUnit or page scaling")
    pdf_matrix = pymupdf.Matrix(1, 0, 0, -1, -raw_crop.x0, raw_crop.y1)
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
        "pdf_to_mupdf_matrix": list(pdf_matrix),
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
        raise PageRangeError(f"Invalid pages {spec!r}; use 1-based ranges within 1-{page_count}") from None
    return sorted(pages)
