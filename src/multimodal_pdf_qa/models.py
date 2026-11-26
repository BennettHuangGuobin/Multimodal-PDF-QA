"""Shared schema. Region boxes are rendered-image pixels, top-left origin."""

from dataclasses import asdict, dataclass, field
from typing import Literal

StageStatus = Literal["not_run", "success", "failed"]
BBox = tuple[float, float, float, float]


@dataclass
class ProcessingError:
    stage: str
    message: str
    error_type: str


@dataclass
class TextBlock:
    id: str
    text: str
    bbox: BBox
    source: str
    confidence: float | None = None
    reading_order: int | None = None
    layout_type: str | None = None
    related_image_ids: list[str] = field(default_factory=list)


@dataclass
class ImageRegion:
    id: str
    bbox: BBox
    source: str
    confidence: float | None = None
    reading_order: int | None = None
    layout_type: str | None = None


@dataclass
class Relation:
    source_id: str
    target_id: str
    relation_type: str
    source: str
    confidence: float | None = None


@dataclass
class PageRecord:
    document_id: str
    pdf_page: int
    render_dpi: int
    page_image_path: str
    printed_page: str | int | None = None
    page_width: float | None = None
    page_height: float | None = None
    image_width: int | None = None
    image_height: int | None = None
    rotation: int | None = None
    media_box: list[float] | None = None
    crop_box: list[float] | None = None
    pdf_to_mupdf_matrix: list[float] | None = None
    mupdf_to_image_matrix: list[float] | None = None
    page_image_sha256: str | None = None
    text_blocks: list[TextBlock] = field(default_factory=list)
    image_regions: list[ImageRegion] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    render_status: StageStatus = "not_run"
    ocr_status: StageStatus = "not_run"
    layout_status: StageStatus = "not_run"
    errors: list[ProcessingError] = field(default_factory=list)
    schema_version: str = "1.0"
    bbox_coordinate_system: str = "image_pixels_top_left_xyxy"

    def to_dict(self) -> dict:
        return asdict(self)
