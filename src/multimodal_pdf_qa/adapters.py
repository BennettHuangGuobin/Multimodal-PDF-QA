"""Backend contracts; importing this module never imports a model framework."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import (ImageRegion, LayoutRegion, PageRecord, PageText, ProcessingError,
                     Relation, StageStatus, TextBlock)


@dataclass
class ParserResult:
    text_blocks: list[TextBlock] = field(default_factory=list)
    image_regions: list[ImageRegion] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    layout_regions: list[LayoutRegion] = field(default_factory=list)
    page_text: PageText | None = None
    ocr_status: StageStatus = "not_run"
    layout_status: StageStatus = "not_run"
    errors: list[ProcessingError] = field(default_factory=list)
    ocr_empty_reason: str | None = None
    layout_empty_reason: str | None = None


class DocumentParserAdapter(Protocol):
    """Implementations normalize all bboxes to the supplied image's pixel space.

    An OCR-only adapter leaves layout_status=not_run, and vice versa.
    Region IDs are unique within a page; relations reference those IDs.
    Runtime/model loading belongs in the future implementation, not this module.
    """

    name: str
    stages: tuple[str, ...]

    def parse(self, image_path: Path, page: PageRecord) -> ParserResult: ...
