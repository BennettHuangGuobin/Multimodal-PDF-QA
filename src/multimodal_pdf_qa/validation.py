"""Pure runtime validation and stage replacement; no model loading or file I/O."""

from copy import deepcopy
from dataclasses import asdict
import json
import math
import re

from .adapters import ParserResult
from .models import (ImageRegion, LayoutRegion, PageRecord, PageText, ParserProvenance,
                     ProcessingError, Relation, TextBlock)

STAGE_FIELDS = {"ocr": ("text_blocks", "page_text"),
                "layout": ("image_regions", "layout_regions")}
V11_FIELDS = {"layout_regions", "page_text", "parser_provenance"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def string(value, name: str) -> None:
    require(isinstance(value, str) and bool(value.strip()), f"{name} must be a nonempty string")


def finite_number(value) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def confidence(value) -> None:
    require(value is None or (finite_number(value) and 0 <= value <= 1),
            "confidence must be null or a finite number in [0,1]")


def validate_adapter(name, stages) -> tuple[str, ...]:
    string(name, "adapter.name")
    require(isinstance(stages, tuple) and bool(stages)
            and all(s in ("ocr", "layout") for s in stages)
            and len(set(stages)) == len(stages), "adapter.stages must be a unique tuple of ocr/layout")
    return stages


def stage_has_data(value, stage: str) -> bool:
    return any(bool(getattr(value, field)) for field in STAGE_FIELDS[stage])


def validate_payload(value, width: int | None, height: int | None) -> None:
    """Validate types, pixel boxes and the complete page's reference graph."""
    json.dumps(asdict(value), allow_nan=False)  # also rejects non-finite extension values
    ids, image_ids = set(), set()
    for field, region_type in (("text_blocks", TextBlock), ("image_regions", ImageRegion),
                                ("layout_regions", LayoutRegion)):
        regions = getattr(value, field)
        require(isinstance(regions, list), f"{field} must be a list")
        for region in regions:
            require(isinstance(region, region_type), f"{field} has an invalid item type")
            string(region.id, "region.id")
            require(region.id not in ids, f"duplicate region ID: {region.id}")
            ids.add(region.id)
            if field == "image_regions":
                image_ids.add(region.id)
            string(region.source, "source")
            confidence(region.confidence)
            require(region.reading_order is None or (type(region.reading_order) is int
                    and region.reading_order >= 1), "reading_order must be null or a positive integer")
            if isinstance(region, LayoutRegion) or region.layout_type is not None:
                string(region.layout_type, "layout_type")
            box = region.bbox
            require(isinstance(box, (list, tuple)) and len(box) == 4
                    and all(finite_number(v) for v in box), "bbox must contain four finite numbers")
            require(type(width) is int and width > 0 and type(height) is int and height > 0,
                    "regions require actual rendered image dimensions")
            x0, y0, x1, y1 = box
            require(0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height,
                    f"bbox outside image bounds or inverted: {box}")
            if isinstance(region, TextBlock):
                string(region.text, "text")
    if value.page_text is not None:
        require(isinstance(value.page_text, PageText), "page_text must be PageText or null")
        string(value.page_text.text, "page_text.text")
        string(value.page_text.source, "page_text.source")
        confidence(value.page_text.confidence)
    for block in value.text_blocks:
        refs = block.related_image_ids
        require(isinstance(refs, list) and all(isinstance(v, str) for v in refs),
                "related_image_ids must be a list of strings")
        require(len(set(refs)) == len(refs) and set(refs) <= image_ids,
                "related_image_ids contains duplicate or missing image references")
    require(isinstance(value.relations, list), "relations must be a list")
    relation_keys = set()
    for relation in value.relations:
        require(isinstance(relation, Relation), "relations must contain Relation objects")
        for field in ("source_id", "target_id", "source", "relation_type"):
            string(getattr(relation, field), f"relation.{field}")
        require(relation.stage in ("ocr", "layout"), "relation.stage must be ocr or layout")
        require(relation.source_id in ids and relation.target_id in ids,
                "relation references a missing region ID")
        confidence(relation.confidence)
        key = (relation.stage, relation.source_id, relation.target_id, relation.relation_type)
        require(key not in relation_keys, "duplicate relation")
        relation_keys.add(key)
    require(isinstance(value.errors, list), "errors must be a list")
    for error in value.errors:
        require(isinstance(error, ProcessingError), "errors must contain ProcessingError objects")
        for field in ("stage", "message", "error_type"):
            string(getattr(error, field), f"error.{field}")


def validate_page_payload(page: PageRecord) -> None:
    validate_payload(page, page.image_width, page.image_height)
    require(isinstance(page.parser_provenance, dict), "parser_provenance must be an object")
    require(set(page.parser_provenance) <= STAGE_FIELDS.keys(), "unknown parser provenance stage")
    for stage in STAGE_FIELDS:
        status = getattr(page, f"{stage}_status")
        require(status in ("not_run", "success", "failed"), f"invalid {stage}_status")
        has_data = stage_has_data(page, stage)
        owned_relations = any(r.stage == stage for r in page.relations)
        provenance = page.parser_provenance.get(stage)
        if page.schema_version == "1.0":
            # Old records remain readable, but absence of execution evidence is
            # never upgraded into a new verified success by the runner.
            continue
        if provenance is not None:
            require(isinstance(provenance, ParserProvenance), "invalid ParserProvenance")
            string(provenance.adapter, "provenance.adapter")
            require(isinstance(provenance.image_sha256, str)
                    and re.fullmatch(r"[0-9a-f]{64}", provenance.image_sha256) is not None,
                    "invalid provenance image hash")
            require(provenance.image_sha256 == page.page_image_sha256,
                    f"{stage} results refer to a different rendered image")
            if provenance.empty_reason is not None:
                string(provenance.empty_reason, "empty_reason")
            require(bool(provenance.empty_reason) != has_data,
                    "empty_reason required exactly when accepted stage output has no content")
        if status == "not_run":
            require(not has_data and not owned_relations and provenance is None,
                    f"{stage}=not_run cannot contain parser results")
            require(not any(e.stage == stage for e in page.errors),
                    f"{stage}=not_run cannot contain an unresolved stage error")
        elif status == "success":
            require(page.render_status == "success" and provenance is not None,
                    f"{stage}=success requires rendered input and accepted parser provenance")
            require(not any(e.stage == stage for e in page.errors),
                    f"{stage}=success cannot contain an unresolved stage error")
        else:
            require(any(e.stage == stage for e in page.errors), f"{stage}=failed requires an error")
            require(not (has_data or owned_relations) or provenance is not None,
                    "retained data after failure requires previous accepted provenance")


def validate_parser_result(result: ParserResult, page: PageRecord, *,
                           adapter_name: str, stages: tuple[str, ...]) -> PageRecord:
    """Return a validated, independently merged candidate page; no file writes.

    Active stages replace their last accepted output, never append. References
    are checked against the final combined page, including preserved stages.
    """
    validate_adapter(adapter_name, stages)
    require(isinstance(result, ParserResult), "adapter must return ParserResult")
    require(page.render_status == "success", "parser requires a successfully rendered page")
    json.dumps(asdict(result), allow_nan=False)
    for field in ("text_blocks", "image_regions", "layout_regions"):
        require(isinstance(getattr(result, field), list), f"{field} must be a list")
    require(result.page_text is None or isinstance(result.page_text, PageText),
            "page_text must be PageText or null")
    require(isinstance(result.errors, list) and all(isinstance(e, ProcessingError) for e in result.errors),
            "ParserResult.errors must contain ProcessingError objects")
    require(isinstance(result.relations, list) and all(isinstance(r, Relation) for r in result.relations),
            "ParserResult.relations must contain Relation objects")
    candidate = deepcopy(page)
    candidate.schema_version = "1.1"
    for stage in STAGE_FIELDS:
        status = getattr(result, f"{stage}_status")
        reason = getattr(result, f"{stage}_empty_reason")
        require(status in ("not_run", "success", "failed"), f"invalid {stage}_status")
        if reason is not None:
            string(reason, f"{stage}_empty_reason")
        if stage not in stages:
            require(status == "not_run", f"adapter cannot update unowned {stage} stage")
        else:
            require(status != "not_run", f"declared {stage} stage must explicitly succeed or fail")
        if status != "success":
            require(not stage_has_data(result, stage) and reason is None
                    and not any(r.stage == stage for r in result.relations),
                    f"{stage} output requires success; partial failed results are not accepted")
        else:
            require(bool(reason) != stage_has_data(result, stage),
                    f"{stage} success without content requires an explicit empty_reason")
        stage_errors = [e for e in result.errors if e.stage == stage]
        require(bool(stage_errors) == (status == "failed"),
                f"{stage} errors must correspond exactly to failed status")
        if stage not in stages:
            continue
        candidate.errors = [e for e in candidate.errors if e.stage != stage]
        candidate.errors.extend(deepcopy(stage_errors))
        setattr(candidate, f"{stage}_status", status)
        if status == "success":
            for field in STAGE_FIELDS[stage]:
                setattr(candidate, field, deepcopy(getattr(result, field)))
            candidate.relations = [r for r in candidate.relations if r.stage != stage]
            candidate.relations.extend(deepcopy([r for r in result.relations if r.stage == stage]))
            candidate.parser_provenance[stage] = ParserProvenance(
                adapter_name, page.page_image_sha256, reason)
    require(all(e.stage in stages for e in result.errors), "error stage is not owned by adapter")
    require(all(r.stage in stages for r in result.relations), "relation stage is not owned by adapter")
    validate_page_payload(candidate)
    return candidate
