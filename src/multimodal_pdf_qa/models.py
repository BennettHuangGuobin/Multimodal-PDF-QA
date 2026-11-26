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
class LayoutRegion:
    """A generic layout area, not necessarily an image (title/table/etc.)."""

    id: str
    bbox: BBox
    source: str
    layout_type: str
    confidence: float | None = None
    reading_order: int | None = None


@dataclass
class PageText:
    """Page-level OCR text when the backend cannot provide reliable boxes."""

    text: str
    source: str
    confidence: float | None = None


@dataclass
class ParserProvenance:
    """Last accepted result for a stage, bound to the actual input PNG."""

    adapter: str
    image_sha256: str
    empty_reason: str | None = None


@dataclass
class Relation:
    source_id: str
    target_id: str
    relation_type: str
    source: str
    confidence: float | None = None
    stage: Literal["ocr", "layout"] = "layout"


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
    layout_regions: list[LayoutRegion] = field(default_factory=list)
    page_text: PageText | None = None
    parser_provenance: dict[str, ParserProvenance] = field(default_factory=dict)
    render_status: StageStatus = "not_run"
    ocr_status: StageStatus = "not_run"
    layout_status: StageStatus = "not_run"
    errors: list[ProcessingError] = field(default_factory=list)
    schema_version: str = "1.1"
    bbox_coordinate_system: str = "image_pixels_top_left_xyxy"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "PageRecord":
        """Decode nested types; v1.0 additions default empty, never invent data.

        Callers still validate core fields and payload semantics before use.
        Unknown fields fail explicitly instead of being silently discarded.
        """
        if not isinstance(value, dict):
            raise ValueError("page record must be an object")
        data = dict(value)
        for name, item_type in (("text_blocks", TextBlock), ("image_regions", ImageRegion),
                                ("layout_regions", LayoutRegion), ("relations", Relation),
                                ("errors", ProcessingError)):
            items = data.get(name, [])
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise ValueError(f"{name} must be a list of objects")
            data[name] = [item_type(**item) for item in items]
        if data.get("page_text") is not None:
            data["page_text"] = PageText(**data["page_text"])
        provenance = data.get("parser_provenance", {})
        if not isinstance(provenance, dict) or not all(isinstance(item, dict) for item in provenance.values()):
            raise ValueError("parser_provenance must be an object of objects")
        data["parser_provenance"] = {stage: ParserProvenance(**item) for stage, item in provenance.items()}
        return cls(**data)
