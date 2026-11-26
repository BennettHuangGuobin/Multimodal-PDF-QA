from pathlib import Path

import pymupdf
import pytest


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    with pymupdf.open() as doc:
        doc.set_metadata({"title": "Synthetic QA report", "author": "pytest"})
        for number in range(1, 5):
            page = doc.new_page(width=240, height=320)
            page.insert_text((25, 45), f"Synthetic page {number}")
            page.draw_rect(pymupdf.Rect(40, 90, 120, 180), fill=(1, 0, 0), color=None)
            if number == 2:
                page.set_rotation(90)
            elif number == 3:
                page.set_cropbox(pymupdf.Rect(20, 30, 220, 290))
            elif number == 4:
                page.set_cropbox(pymupdf.Rect(20, 30, 220, 290))
                page.set_rotation(270)
        doc.save(path)
    return path
