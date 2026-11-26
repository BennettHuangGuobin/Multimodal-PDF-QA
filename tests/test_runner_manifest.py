"""Runner preflight must reject unsafe manifests without any side effects."""

import json

import pytest

from multimodal_pdf_qa.prepare import prepare_pdf
from multimodal_pdf_qa.runner import run_parser
from mock_adapters import SyntheticAdapter


def snapshot(directory):
    return {str(p.relative_to(directory)): p.read_bytes()
            for p in directory.rglob("*") if p.is_file()}


@pytest.mark.parametrize("changes", [
    {"schema_version": "unsupported"}, {"status": "unknown"},
    {"expected_page_count": 999}, {"expected_page_count": True},
    {"successful_render_count": 0}, {"failed_render_count": 1},
    {"pending_render_count": 1}, {"failed_render_count": False},
    {"status": "running", "successful_render_count": 0, "pending_render_count": 3},
    {"status": "running", "successful_render_count": 2, "pending_render_count": 0},
    {"status": "running", "failed_render_count": -1},
    {"status": "incomplete", "successful_render_count": 2},
])
def test_bad_manifest_rejected_before_adapter_or_writes(sample_pdf, tmp_path, changes):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1-3")
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest.update(changes)
    path.write_text(json.dumps(manifest))
    before = snapshot(directory)
    adapter = SyntheticAdapter()
    with pytest.raises(ValueError, match="Invalid manifest.*manifest\\."):
        run_parser(directory, adapter, pages="1")
    assert adapter.calls == []
    assert snapshot(directory) == before  # includes audit.json, PNGs and file inventory


@pytest.mark.parametrize("field", ["schema_version", "status", "expected_page_count",
                                  "successful_render_count", "failed_render_count"])
def test_missing_manifest_field_rejected_before_adapter_or_writes(sample_pdf, tmp_path, field):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1")
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    del manifest[field]
    path.write_text(json.dumps(manifest))
    before = snapshot(directory)
    adapter = SyntheticAdapter()
    with pytest.raises(ValueError, match=f"manifest.{field}"):
        run_parser(directory, adapter)
    assert adapter.calls == [] and snapshot(directory) == before


@pytest.mark.parametrize("mode", ["running", "incomplete", "legacy_running", "legacy_complete"])
def test_recoverable_manifest_is_allowed_without_rewriting_it(sample_pdf, tmp_path, mode):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1")
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["status"] = mode.removeprefix("legacy_")
    if mode == "legacy_running":
        for field in ("successful_render_count", "failed_render_count", "pending_render_count"):
            del manifest[field]
    elif mode == "legacy_complete":
        del manifest["pending_render_count"]
    path.write_text(json.dumps(manifest))
    before = path.read_bytes()
    adapter = SyntheticAdapter()
    report = run_parser(directory, adapter)
    assert report["parsed_pages"] == [1] and report["audit"]["ok"]
    assert path.read_bytes() == before and adapter.calls == [1]
    if mode != "legacy_complete":
        assert report["audit"]["warnings"]


def test_real_running_checkpoint_can_parse_then_resume_prepare(sample_pdf, tmp_path, monkeypatch):
    import multimodal_pdf_qa.prepare as module

    original = module.write_json
    def interrupt(path, value):
        if path.name == "manifest.json" and value["last_run"]["rendered"] == 1:
            raise KeyboardInterrupt
        original(path, value)
    monkeypatch.setattr(module, "write_json", interrupt)
    output = tmp_path / "out"
    with pytest.raises(KeyboardInterrupt):
        prepare_pdf(sample_pdf, output, pages="1-2")
    directory = next(output.iterdir())
    before = (directory / "manifest.json").read_bytes()
    adapter = SyntheticAdapter()
    report = run_parser(directory, adapter)
    assert report["parsed_pages"] == [1] and adapter.calls == [1]
    assert report["skipped_pages"] == [{"pdf_page": 2, "reason": "render_not_success"}]
    assert report["audit"]["issues"] == []
    assert any("one-page checkpoint lag" in item for item in report["audit"]["warnings"])
    assert (directory / "manifest.json").read_bytes() == before
    monkeypatch.setattr(module, "write_json", original)
    prepare_pdf(sample_pdf, output, pages="1-2")
    assert run_parser(directory, adapter)["audit"]["ok"]
    assert adapter.calls == [1, 2]


def test_complete_cannot_hide_bad_unrequested_image(sample_pdf, tmp_path):
    directory = prepare_pdf(sample_pdf, tmp_path / "out", pages="1-2")
    (directory / "pages/page_0002.png").unlink()
    before = snapshot(directory)
    adapter = SyntheticAdapter()
    with pytest.raises(ValueError, match="manifest.status=complete.*PDF page 2"):
        run_parser(directory, adapter, pages="1")
    assert adapter.calls == [] and snapshot(directory) == before
