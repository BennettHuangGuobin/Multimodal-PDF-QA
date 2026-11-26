from dataclasses import replace
import json

import pytest

from multimodal_pdf_qa.adapters import ParserResult
from multimodal_pdf_qa.models import PageRecord, PageText, ProcessingError
from multimodal_pdf_qa.prepare import prepare_pdf
from multimodal_pdf_qa.validation import validate_parser_result
from mock_adapters import SOURCE, joint_result, ocr_result


@pytest.fixture
def context(sample_pdf, tmp_path):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1")
    return PageRecord.from_dict(json.loads((directory / "pages.jsonl").read_text()))


@pytest.mark.parametrize("box", [[], [0, 0, 1], [0, 0, 1, 2, 3], "0,0,1,1",
                                 [0, 0, float("nan"), 2], [0, 0, float("inf"), 2],
                                 [False, 0, 1, 2], [-1, 0, 1, 2], [2, 0, 1, 2],
                                 [0, 2, 1, 2], [0, 0, 9999, 2]])
def test_reject_bad_bbox(context, box):
    result = ocr_result(context)
    result.text_blocks[0].bbox = box
    with pytest.raises(ValueError):
        validate_parser_result(result, context, adapter_name=SOURCE, stages=("ocr",))


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf"), True, "0.5"])
def test_reject_bad_confidence(context, value):
    result = ocr_result(context)
    result.text_blocks[0].confidence = value
    with pytest.raises(ValueError):
        validate_parser_result(result, context, adapter_name=SOURCE, stages=("ocr",))


@pytest.mark.parametrize("field,value", [("source", ""), ("source", 1), ("layout_type", 1),
                                          ("layout_type", ""), ("reading_order", 0),
                                          ("reading_order", True), ("reading_order", 1.2),
                                          ("text", ""), ("id", 2)])
def test_reject_bad_text_metadata(context, field, value):
    result = ocr_result(context)
    setattr(result.text_blocks[0], field, value)
    with pytest.raises(ValueError):
        validate_parser_result(result, context, adapter_name=SOURCE, stages=("ocr",))


@pytest.mark.parametrize("damage", ["duplicate_text", "cross_kind_id", "dangling_relation",
                                    "dangling_image", "duplicate_relation", "bad_relation_stage",
                                    "layout_type", "ocr_state", "layout_state", "wrong_item"])
def test_reject_invalid_graph_and_status(context, damage):
    result = joint_result(context)
    if damage == "duplicate_text":
        result.text_blocks.append(replace(result.text_blocks[0]))
    elif damage == "cross_kind_id":
        result.layout_regions[0].id = result.text_blocks[0].id
    elif damage == "dangling_relation":
        result.relations[0].target_id = "missing"
    elif damage == "dangling_image":
        result.text_blocks[0].related_image_ids = ["missing"]
    elif damage == "duplicate_relation":
        result.relations.append(replace(result.relations[0]))
    elif damage == "bad_relation_stage":
        result.relations[0].stage = "unknown"
    elif damage == "layout_type":
        result.layout_regions[0].layout_type = None
    elif damage == "ocr_state":
        result.ocr_status = "unknown"
    elif damage == "layout_state":
        result.layout_status = "unknown"
    else:
        result.text_blocks = [{}]
    with pytest.raises(ValueError):
        validate_parser_result(result, context, adapter_name=SOURCE, stages=("ocr", "layout"))


@pytest.mark.parametrize("result", [ParserResult(), ParserResult(ocr_status="success"),
    ParserResult(ocr_status="failed"),
    ParserResult(ocr_status="not_run", page_text=PageText("synthetic", SOURCE)),
    ParserResult(ocr_status="success", ocr_empty_reason=" "),
    ParserResult(ocr_status="success", ocr_empty_reason="empty", errors=[ProcessingError("ocr", "bad", "Error")]),
    ParserResult(ocr_status="success", ocr_empty_reason="empty", layout_status="success", layout_empty_reason="empty"),
    ParserResult(ocr_status="success", ocr_empty_reason="empty", text_blocks={}),
    ParserResult(ocr_status="success", ocr_empty_reason="empty", page_text={}),
])
def test_no_implicit_or_unowned_success(context, result):
    with pytest.raises(ValueError):
        validate_parser_result(result, context, adapter_name=SOURCE, stages=("ocr",))


def test_valid_page_text_and_explicit_empty_result(context):
    result = ParserResult(ocr_status="success", page_text=PageText("Synthetic unboxed text", SOURCE))
    merged = validate_parser_result(result, context, adapter_name=SOURCE, stages=("ocr",))
    assert merged.page_text.text == "Synthetic unboxed text"
    assert merged.text_blocks == [] and "bbox" not in merged.to_dict()["page_text"]
    empty = ParserResult(ocr_status="success", ocr_empty_reason="Backend ran; no text detected in synthetic input")
    merged = validate_parser_result(empty, context, adapter_name=SOURCE, stages=("ocr",))
    assert merged.ocr_status == "success" and merged.parser_provenance["ocr"].empty_reason
    assert context.ocr_status == "not_run"  # pure validation never mutates input


@pytest.mark.parametrize("kind", ["image", "layout", "relation", "page_text"])
def test_confidence_checked_on_all_output_types(context, kind):
    result = joint_result(context)
    result.page_text = PageText("Synthetic page text", SOURCE)
    objects = {"image": result.image_regions[0], "layout": result.layout_regions[0],
               "relation": result.relations[0], "page_text": result.page_text}
    objects[kind].confidence = 1.1
    with pytest.raises(ValueError, match="confidence"):
        validate_parser_result(result, context, adapter_name=SOURCE, stages=("ocr", "layout"))


@pytest.mark.parametrize("stages", [(), ("ocr", "ocr"), ("unknown",), ["ocr"]])
def test_adapter_must_declare_valid_stages(context, stages):
    with pytest.raises(ValueError, match="adapter.stages"):
        validate_parser_result(ocr_result(context), context, adapter_name=SOURCE, stages=stages)
