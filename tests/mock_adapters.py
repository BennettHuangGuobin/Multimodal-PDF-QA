"""Deterministic synthetic test data ONLY; never a model implementation."""

import pymupdf

from multimodal_pdf_qa.adapters import ParserResult
from multimodal_pdf_qa.models import ImageRegion, LayoutRegion, Relation, TextBlock

SOURCE = "synthetic-test:manually-defined"


def ocr_result(page):
    return ParserResult(ocr_status="success", text_blocks=[
        TextBlock("ocr:text", "Synthetic test text, not OCR", (10, 10, 40, 30), SOURCE,
                  confidence=0.9, reading_order=1)])


def layout_result(page):
    return ParserResult(layout_status="success", layout_regions=[
        LayoutRegion("layout:title", (5, 5, 80, 35), SOURCE, "title", reading_order=1),
        LayoutRegion("layout:table", (5, 40, 100, 100), SOURCE, "table", reading_order=2)],
        image_regions=[ImageRegion("layout:image", (110, 40, 150, 100), SOURCE)],
        relations=[Relation("layout:title", "layout:table", "above", SOURCE)])


def joint_result(page):
    result = ocr_result(page)
    layout = layout_result(page)
    result.layout_status = "success"
    result.layout_regions = layout.layout_regions
    result.image_regions = layout.image_regions
    result.relations = layout.relations
    return result


class SyntheticAdapter:
    def __init__(self, stages=("ocr",), factory=ocr_result, name="synthetic-test/ocr-v1"):
        self.stages, self.factory, self.name = stages, factory, name
        self.calls = []

    def parse(self, image_path, page):
        pix = pymupdf.Pixmap(image_path)
        assert (pix.width, pix.height) == (page.image_width, page.image_height)
        self.calls.append(page.pdf_page)
        return self.factory(page)
