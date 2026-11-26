import json
from pathlib import Path

import pytest

from multimodal_pdf_qa.adapters import ParserResult
from multimodal_pdf_qa.audit import audit_document
from multimodal_pdf_qa.models import PageRecord, PageText, ProcessingError, Relation
from multimodal_pdf_qa.prepare import prepare_pdf
from multimodal_pdf_qa.runner import run_parser
from multimodal_pdf_qa.validation import V11_FIELDS
from mock_adapters import SOURCE, SyntheticAdapter, joint_result, layout_result, ocr_result


def rows(directory):
    return [json.loads(line) for line in (directory / "pages.jsonl").read_text().splitlines()]


@pytest.fixture
def prepared(sample_pdf, tmp_path):
    return prepare_pdf(sample_pdf, tmp_path / "processed", pages="1")


@pytest.mark.parametrize("order", [("ocr", "layout"), ("layout", "ocr")])
def test_stage_independence_and_repeat(prepared, order):
    adapters = {"ocr": SyntheticAdapter(),
                "layout": SyntheticAdapter(("layout",), layout_result, "synthetic-test/layout-v1")}
    first, second = order
    assert run_parser(prepared, adapters[first])["parsed_pages"] == [1]
    before = rows(prepared)[0]
    assert run_parser(prepared, adapters[second])["audit"]["ok"]
    after = rows(prepared)[0]
    fields = ("text_blocks", "page_text") if first == "ocr" else ("layout_regions", "image_regions")
    for field in fields:
        assert after[field] == before[field]
    assert after["parser_provenance"][first] == before["parser_provenance"][first]
    assert after["ocr_status"] == after["layout_status"] == "success"
    assert {r["layout_type"] for r in after["layout_regions"]} == {"title", "table"}
    index_before = (prepared / "pages.jsonl").read_bytes()
    assert run_parser(prepared, adapters[first])["skipped_pages"][0]["reason"] == "already_parsed"
    assert (prepared / "pages.jsonl").read_bytes() == index_before
    run_parser(prepared, adapters[first], force=True)
    assert (prepared / "pages.jsonl").read_bytes() == index_before
    assert len(adapters[first].calls) == 2


def test_cross_stage_relations_and_conflicting_replacement(prepared):
    run_parser(prepared, SyntheticAdapter(("layout",), layout_result, "synthetic-test/layout-v1"))
    def linked_ocr(page):
        result = ocr_result(page)
        result.text_blocks[0].related_image_ids = ["layout:image"]
        result.relations = [Relation("ocr:text", "layout:title", "caption_of", SOURCE, stage="ocr")]
        return result
    run_parser(prepared, SyntheticAdapter(factory=linked_ocr))
    before = rows(prepared)[0]
    def new_layout(page):
        result = layout_result(page)
        result.layout_regions[0].id = "layout:new-title"
        result.relations[0].source_id = "layout:new-title"
        return result
    report = run_parser(prepared, SyntheticAdapter(("layout",), new_layout, "synthetic-test/layout-v2"))
    after = rows(prepared)[0]
    assert report["failed_pages"] == [1]
    assert after["layout_regions"] == before["layout_regions"]
    assert after["relations"] == before["relations"]
    assert after["text_blocks"] == before["text_blocks"] and after["ocr_status"] == "success"
    assert after["layout_status"] == "failed"
    assert report["audit"]["invalid_parser_records"] == []
    message = after["errors"][0]["message"]
    assert "PDF page 1" in message and "replacing stages ('layout',)" in message
    assert "stage='ocr'" in message and "type='caption_of'" in message
    assert "'ocr:text' -> 'layout:title'" in message
    assert "missing region IDs=['layout:title']" in message and "joint rerun" in message
    assert after["parser_provenance"] == before["parser_provenance"]


@pytest.mark.parametrize("failure", ["bad_bbox", "exception", "wrong_return", "empty_success", "not_run"])
def test_failure_retains_previous_valid_payload_and_recovers(prepared, failure):
    adapter = SyntheticAdapter()
    run_parser(prepared, adapter)
    before = rows(prepared)[0]
    def broken(page):
        if failure == "exception":
            raise RuntimeError("synthetic model failure")
        if failure == "wrong_return":
            return None
        if failure == "empty_success":
            return ParserResult(ocr_status="success")
        if failure == "not_run":
            return ParserResult()
        result = ocr_result(page)
        result.text_blocks[0].bbox = (-1, 0, 10, 10)
        return result
    adapter.factory = broken
    report = run_parser(prepared, adapter, force=True)
    after = rows(prepared)[0]
    assert after["ocr_status"] == "failed" and after["layout_status"] == "not_run"
    assert after["text_blocks"] == before["text_blocks"]
    assert after["parser_provenance"] == before["parser_provenance"]
    assert after["errors"][0]["stage"] == "ocr"
    assert report["failed_pages"] == [1] and not report["audit"]["ok"]
    adapter.factory = ocr_result
    assert run_parser(prepared, adapter)["audit"]["ok"]
    assert len(rows(prepared)[0]["text_blocks"]) == 1
    assert rows(prepared)[0]["errors"] == []


def test_joint_partial_failure_and_later_ocr(prepared):
    def partial(page):
        result = layout_result(page)
        result.ocr_status = "failed"
        result.errors = [ProcessingError("ocr", "synthetic OCR failure", "SyntheticFailure")]
        return result
    report = run_parser(prepared, SyntheticAdapter(("ocr", "layout"), partial))
    assert report["failed_pages"] == [1]
    before = rows(prepared)[0]
    assert before["layout_status"] == "success" and before["ocr_status"] == "failed"
    assert run_parser(prepared, SyntheticAdapter())["audit"]["ok"]
    assert rows(prepared)[0]["layout_regions"] == before["layout_regions"]


def test_page_text_written_without_invented_bbox(prepared):
    adapter = SyntheticAdapter(factory=lambda page: ParserResult(
        ocr_status="success", page_text=PageText("Synthetic page-level text", SOURCE)))
    assert run_parser(prepared, adapter)["audit"]["ok"]
    record = rows(prepared)[0]
    assert record["text_blocks"] == []
    assert "bbox" not in record["page_text"]


def test_explicit_empty_reason_requires_a_real_call(prepared):
    adapter = SyntheticAdapter(factory=lambda page: ParserResult(
        ocr_status="success", ocr_empty_reason="Synthetic backend ran and found no text"))
    run_parser(prepared, adapter)
    assert adapter.calls == [1]
    assert rows(prepared)[0]["parser_provenance"]["ocr"]["empty_reason"]


def test_unrendered_pages_never_enter_adapter(sample_pdf, tmp_path, monkeypatch):
    import multimodal_pdf_qa.prepare as module
    real = module.render_page
    def flaky(page, target, dpi):
        if page.number == 1:
            raise RuntimeError("synthetic render failure")
        return real(page, target, dpi)
    monkeypatch.setattr(module, "render_page", flaky)
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1-2")
    adapter = SyntheticAdapter()
    report = run_parser(directory, adapter)
    assert adapter.calls == [1]
    assert report["skipped_pages"] == [{"pdf_page": 2, "reason": "render_not_success"}]
    assert rows(directory)[1]["ocr_status"] == "not_run"


def test_write_failure_is_atomic_and_not_model_failure(prepared, monkeypatch):
    original = Path.replace
    def fail_index(self, target):
        if Path(target).name == "pages.jsonl":
            raise OSError("synthetic write interruption")
        return original(self, target)
    before = (prepared / "pages.jsonl").read_bytes()
    adapter = SyntheticAdapter()
    monkeypatch.setattr(Path, "replace", fail_index)
    with pytest.raises(OSError, match="write interruption"):
        run_parser(prepared, adapter)
    assert (prepared / "pages.jsonl").read_bytes() == before
    assert not list(prepared.glob(".pages.jsonl.*"))
    monkeypatch.setattr(Path, "replace", original)
    assert run_parser(prepared, adapter)["audit"]["ok"]
    assert adapter.calls == [1, 1] and len(rows(prepared)[0]["text_blocks"]) == 1


def test_interrupt_after_first_checkpoint_resumes(sample_pdf, tmp_path, monkeypatch):
    import multimodal_pdf_qa.runner as module
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1-2")
    original = module.write_jsonl
    def interrupt(path, records):
        if records[2]["ocr_status"] == "success":
            raise KeyboardInterrupt
        original(path, records)
    adapter = SyntheticAdapter()
    monkeypatch.setattr(module, "write_jsonl", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_parser(directory, adapter)
    assert [r["ocr_status"] for r in rows(directory)] == ["success", "not_run"]
    monkeypatch.setattr(module, "write_jsonl", original)
    assert run_parser(directory, adapter)["audit"]["ok"]
    assert adapter.calls == [1, 2, 2]
    assert [len(r["text_blocks"]) for r in rows(directory)] == [1, 1]


@pytest.mark.parametrize("dpi", [72, 144])
def test_force_render_invalidates_all_parser_results(prepared, sample_pdf, dpi):
    adapter = SyntheticAdapter(("ocr", "layout"), joint_result)
    run_parser(prepared, adapter)
    prepare_pdf(sample_pdf, prepared.parent, pages="1", force=True, dpi=dpi)
    record = rows(prepared)[0]
    assert record["ocr_status"] == record["layout_status"] == "not_run"
    assert record["text_blocks"] == record["layout_regions"] == record["image_regions"] == record["relations"] == []
    assert record["parser_provenance"] == {} and record["page_text"] is None
    run_parser(prepared, adapter)
    assert adapter.calls == [1, 1]


@pytest.mark.parametrize("damage", ["bbox", "provenance", "relation", "bad_list", "bad_provenance_type"])
def test_audit_and_runner_reject_bad_persisted_parser_data(prepared, damage):
    run_parser(prepared, SyntheticAdapter())
    record = rows(prepared)[0]
    if damage == "bbox":
        record["text_blocks"][0]["bbox"] = [0, 0, 9999, 9999]
    elif damage == "provenance":
        record["parser_provenance"]["ocr"]["image_sha256"] = "0" * 64
    elif damage == "relation":
        record["text_blocks"][0]["related_image_ids"] = ["missing"]
    elif damage == "bad_list":
        record["layout_regions"] = {}
    else:
        record["parser_provenance"] = []
    path = prepared / "pages.jsonl"
    path.write_text(json.dumps(record) + "\n")
    before = path.read_bytes()
    audit = audit_document(prepared)
    assert audit["render_complete"] and not audit["ok"]
    assert audit["invalid_parser_records"]
    adapter = SyntheticAdapter()
    with pytest.raises(ValueError, match="Invalid page index"):
        run_parser(prepared, adapter)
    assert adapter.calls == [] and path.read_bytes() == before


def test_corrupt_png_cannot_enter_adapter(prepared):
    (prepared / "pages/page_0001.png").write_bytes(b"not a PNG")
    adapter = SyntheticAdapter()
    before = (prepared / "pages.jsonl").read_bytes()
    with pytest.raises(ValueError, match="unusable parser input"):
        run_parser(prepared, adapter)
    assert adapter.calls == []
    assert (prepared / "pages.jsonl").read_bytes() == before


def test_adapter_cannot_mutate_page_context(prepared):
    before = rows(prepared)[0]
    def mutate(page):
        page.document_id = "incorrect"
        page.image_width = 99999
        return ocr_result(page)
    run_parser(prepared, SyntheticAdapter(factory=mutate))
    after = rows(prepared)[0]
    assert after["document_id"] == before["document_id"]
    assert after["image_width"] == before["image_width"]


def test_legacy_empty_index_migrates_only_requested_page(sample_pdf, tmp_path):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1-2")
    old = rows(directory)
    for record in old:
        record["schema_version"] = "1.0"
        for field in V11_FIELDS:
            del record[field]
    path = directory / "pages.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in old))
    assert audit_document(directory)["ok"]
    decoded = PageRecord.from_dict(old[0])
    assert decoded.layout_regions == [] and decoded.parser_provenance == {}
    assert run_parser(directory, SyntheticAdapter(), pages="1")["audit"]["ok"]
    after = rows(directory)
    assert after[0]["schema_version"] == "1.1"
    assert after[1] == old[1]
    prepare_pdf(sample_pdf, directory.parent, pages="1-2")
    assert rows(directory) == after


def test_legacy_manual_parser_data_is_readable_but_not_falsely_migrated(prepared):
    record = rows(prepared)[0]
    record["schema_version"] = "1.0"
    record["ocr_status"] = "success"
    for field in V11_FIELDS:
        del record[field]
    path = prepared / "pages.jsonl"
    path.write_text(json.dumps(record) + "\n")
    assert PageRecord.from_dict(record).ocr_status == "success"
    assert any("legacy parser data" in warning for warning in audit_document(prepared)["warnings"])
    before = path.read_bytes()
    adapter = SyntheticAdapter()
    with pytest.raises(ValueError, match="legacy parsed results"):
        run_parser(prepared, adapter)
    assert adapter.calls == [] and path.read_bytes() == before


def test_input_changed_during_parse_is_not_committed(prepared):
    class MutatingAdapter(SyntheticAdapter):
        def parse(self, image_path, page):
            result = super().parse(image_path, page)
            image_path.write_bytes(b"synthetic backend violates read-only input contract")
            return result
    path = prepared / "pages.jsonl"
    before = path.read_bytes()
    with pytest.raises(ValueError, match="PNG changed during parsing"):
        run_parser(prepared, MutatingAdapter())
    assert path.read_bytes() == before
    assert not audit_document(prepared)["ok"]


def test_changed_backend_identity_replaces_owned_stage(prepared):
    first = SyntheticAdapter()
    run_parser(prepared, first)
    def different_ocr(page):
        result = ocr_result(page)
        result.text_blocks[0].text = "Second synthetic backend"
        return result
    second = SyntheticAdapter(factory=different_ocr, name="synthetic-test/ocr-v2")
    assert run_parser(prepared, second)["parsed_pages"] == [1]
    record = rows(prepared)[0]
    assert len(record["text_blocks"]) == 1
    assert record["text_blocks"][0]["text"] == "Second synthetic backend"
    assert record["parser_provenance"]["ocr"]["adapter"] == second.name


def test_layout_adapter_cannot_return_ocr_outputs(prepared):
    run_parser(prepared, SyntheticAdapter())
    before = rows(prepared)[0]
    report = run_parser(prepared, SyntheticAdapter(("layout",), joint_result, "synthetic-test/bad-layout"))
    after = rows(prepared)[0]
    assert report["failed_pages"] == [1]
    assert after["ocr_status"] == "success" and after["text_blocks"] == before["text_blocks"]
    assert after["layout_status"] == "failed" and after["layout_regions"] == []


def test_new_layout_id_cannot_collide_with_preserved_ocr(prepared):
    run_parser(prepared, SyntheticAdapter())
    before = rows(prepared)[0]
    def collision(page):
        result = layout_result(page)
        result.image_regions[0].id = "ocr:text"
        return result
    run_parser(prepared, SyntheticAdapter(("layout",), collision, "synthetic-test/layout"))
    after = rows(prepared)[0]
    assert after["layout_status"] == "failed" and after["image_regions"] == []
    assert after["text_blocks"] == before["text_blocks"]
    assert "duplicate region ID" in after["errors"][0]["message"]
    assert "'ocr:text'" in after["errors"][0]["message"]
    assert "text_blocks[0] conflicts with image_regions[0]" in after["errors"][0]["message"]


def test_corrupt_unrequested_parser_page_rejected_before_any_write(sample_pdf, tmp_path):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1-2")
    records = rows(directory)
    records[1]["ocr_status"] = "success"  # no accepted payload/provenance
    path = directory / "pages.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    before = {p.name: p.read_bytes() for p in directory.glob("*.json*")}
    adapter = SyntheticAdapter()
    with pytest.raises(ValueError, match="PDF page 2"):
        run_parser(directory, adapter, pages="1")
    assert adapter.calls == []
    assert before == {p.name: p.read_bytes() for p in directory.glob("*.json*")}


def test_legacy_image_regions_keep_their_meaning(prepared):
    from multimodal_pdf_qa.validation import validate_page_payload

    record = rows(prepared)[0]
    record["schema_version"] = "1.0"
    for field in V11_FIELDS:
        del record[field]
    record["image_regions"] = [dict(id="legacy:image", bbox=[0, 0, 10, 10], source=SOURCE)]
    record["relations"] = [dict(source_id="legacy:image", target_id="legacy:image",
                                relation_type="synthetic_self", source=SOURCE)]
    record["layout_status"] = "success"
    decoded = PageRecord.from_dict(record)
    validate_page_payload(decoded)
    assert decoded.image_regions[0].id == "legacy:image" and decoded.layout_regions == []
    assert decoded.relations[0].stage == "layout"


@pytest.mark.parametrize("keep_id", [True, False])
def test_layout_image_update_preserves_or_rejects_ocr_links(prepared, keep_id):
    def linked(page):
        result = joint_result(page)
        result.text_blocks[0].related_image_ids = ["layout:image"]
        return result
    run_parser(prepared, SyntheticAdapter(("ocr", "layout"), linked))
    before = rows(prepared)[0]
    def changed(page):
        result = layout_result(page)
        result.image_regions[0].bbox = (115, 40, 155, 100)
        if not keep_id:
            result.image_regions[0].id = "layout:new-image"
        return result
    report = run_parser(prepared, SyntheticAdapter(("layout",), changed, "synthetic-test/layout-v2"))
    after = rows(prepared)[0]
    assert after["text_blocks"] == before["text_blocks"] and after["ocr_status"] == "success"
    if keep_id:
        assert report["parsed_pages"] == [1] and report["audit"]["ok"]
        assert after["image_regions"] != before["image_regions"]
    else:
        assert report["failed_pages"] == [1] and after["layout_status"] == "failed"
        assert after["image_regions"] == before["image_regions"]
        assert after["parser_provenance"] == before["parser_provenance"]
        assert "TextBlock 'ocr:text'.related_image_ids: missing ImageRegion IDs=['layout:image']" in after["errors"][0]["message"]


def test_joint_rerun_can_update_regions_and_their_reference_owners(prepared):
    def linked(page):
        result = joint_result(page)
        result.text_blocks[0].related_image_ids = ["layout:image"]
        result.relations.append(Relation("ocr:text", "layout:title", "describes", SOURCE, stage="ocr"))
        return result
    run_parser(prepared, SyntheticAdapter(("ocr", "layout"), linked))
    def renamed(page):
        result = linked(page)
        for region in result.text_blocks + result.image_regions + result.layout_regions:
            region.id += ":v2"
        for block in result.text_blocks:
            block.related_image_ids = [name + ":v2" for name in block.related_image_ids]
        for relation in result.relations:
            relation.source_id += ":v2"
            relation.target_id += ":v2"
        return result
    report = run_parser(prepared, SyntheticAdapter(("ocr", "layout"), renamed, "synthetic-test/joint-v2"))
    record = rows(prepared)[0]
    assert report["parsed_pages"] == [1] and report["audit"]["ok"]
    assert record["ocr_status"] == record["layout_status"] == "success"
    assert all(region["id"].endswith(":v2") for name in ("text_blocks", "image_regions", "layout_regions")
               for region in record[name])
    assert record["text_blocks"][0]["related_image_ids"] == ["layout:image:v2"]
    assert len(record["relations"]) == 2 and record["errors"] == []


def test_ocr_id_change_cannot_break_preserved_layout_relation(prepared):
    def linked(page):
        result = joint_result(page)
        result.relations.append(Relation("layout:title", "ocr:text", "contains", SOURCE))
        return result
    run_parser(prepared, SyntheticAdapter(("ocr", "layout"), linked))
    before = rows(prepared)[0]
    def renamed_ocr(page):
        result = ocr_result(page)
        result.text_blocks[0].id = "ocr:replacement"
        return result
    report = run_parser(prepared, SyntheticAdapter(factory=renamed_ocr, name="synthetic-test/ocr-v2"))
    after = rows(prepared)[0]
    assert report["failed_pages"] == [1] and after["ocr_status"] == "failed"
    assert after["layout_status"] == "success"
    for field in ("text_blocks", "layout_regions", "image_regions", "relations", "parser_provenance"):
        assert after[field] == before[field]
    assert "stage='layout'" in after["errors"][0]["message"]
    assert "'layout:title' -> 'ocr:text'" in after["errors"][0]["message"]
    assert "missing region IDs=['ocr:text']" in after["errors"][0]["message"]
