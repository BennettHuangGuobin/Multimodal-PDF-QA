"""Synthetic HTTP responses only: these tests never contact a model service."""

import base64
import importlib.util
from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from multimodal_pdf_qa.backends import ollama_ocr as backend
from multimodal_pdf_qa.prepare import prepare_pdf
from multimodal_pdf_qa.runner import run_parser
from mock_adapters import SyntheticAdapter, layout_result

TEXT = "Synthetic HTTP fixture text\n第二行：合成测试，不是真实 OCR\n"


def response_text(text=TEXT):
    return {"model": backend.DEFAULT_MODEL, "response": text, "done": True, "done_reason": "stop",
            "total_duration": 20_000_000, "load_duration": 1_000_000,
            "prompt_eval_duration": 2_000_000, "eval_duration": 17_000_000,
            "prompt_eval_count": 12, "eval_count": 20}


class FakeHTTP:
    def __init__(self):
        self.response = response_text()
        self.version = {"version": "test-version"}
        self.tags = {"models": [{"name": backend.DEFAULT_MODEL, "digest": "a" * 64}]}
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        endpoint = request.full_url.split("/api/")[-1]
        value = {"version": self.version, "tags": self.tags, "generate": self.response}[endpoint]
        if isinstance(value, BaseException):
            raise value
        return BytesIO(value if isinstance(value, bytes) else json.dumps(value).encode())

    @property
    def generations(self):
        return [req for req, _ in self.requests if req.full_url.endswith("/api/generate")]


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(backend, "build_opener", lambda *args: fake)
    return fake


@pytest.fixture
def prepared(sample_pdf, tmp_path):
    return prepare_pdf(sample_pdf, tmp_path / "data/experiment", pages="1")


def record(directory):
    return json.loads((directory / "pages.jsonl").read_text())


def test_protocol_text_identity_and_metrics(http, prepared):
    options = {"temperature": 0.1}
    adapter = backend.OllamaOCRAdapter(timeout=45, options=options)
    options["temperature"] = 99
    run = run_parser(prepared, adapter)
    row = record(prepared)
    assert run["audit"]["ok"] and row["ocr_status"] == "success"
    assert row["page_text"]["text"] == TEXT and row["text_blocks"] == []
    assert row["layout_status"] == "not_run" and row["relations"] == []
    assert "bbox" not in row["page_text"]
    assert "a" * 64 in adapter.name and "server=test-version" in adapter.name
    assert row["parser_provenance"]["ocr"]["adapter"] == row["page_text"]["source"] == adapter.name
    payload = json.loads(http.generations[0].data)
    assert payload["model"] == backend.DEFAULT_MODEL and payload["prompt"] == "Text Recognition:"
    assert payload["stream"] is False and payload["raw"] is False
    assert payload["options"] == {"temperature": 0.1, "num_ctx": 16384, "num_predict": 8192}
    assert base64.b64decode(payload["images"][0]) == (prepared / row["page_image_path"]).read_bytes()
    assert all(timeout == 45 for _, timeout in http.requests)
    attempt = adapter.attempts[0]
    assert attempt["character_count"] == len(TEXT)
    assert attempt["metrics"]["total_duration"] == 20_000_000
    assert attempt["metrics"]["prompt_eval_cached_count"] is None
    assert attempt["elapsed_seconds"] >= 0 and attempt["structured_bbox_returned"] is False
    identity = adapter.identity
    identity["options"]["temperature"] = 99
    assert adapter.identity["options"]["temperature"] == 0.1
    assert backend.OllamaOCRAdapter(timeout=45.0, options={"temperature": 0.1}).name == adapter.name


@pytest.mark.parametrize("text", ["", " \n\t"])
def test_blank_response_is_not_declared_empty_success(http, prepared, text):
    http.response = response_text(text)
    adapter = backend.OllamaOCRAdapter()
    assert run_parser(prepared, adapter)["failed_pages"] == [1]
    row = record(prepared)
    assert row["ocr_status"] == "failed" and row["page_text"] is None
    assert row["errors"][0]["error_type"] == "OllamaResponseError"
    assert adapter.attempts[0]["character_count"] == len(text)


def test_explicit_empty_marker_requires_prompt_contract(http, prepared):
    http.response = response_text("<NO_TEXT>")
    adapter = backend.OllamaOCRAdapter(prompt="Text Recognition: If no text, return <NO_TEXT>.",
                                       empty_marker="<NO_TEXT>")
    assert run_parser(prepared, adapter)["audit"]["ok"]
    row = record(prepared)
    assert row["page_text"] is None and row["text_blocks"] == []
    assert "model assertion" in row["parser_provenance"]["ocr"]["empty_reason"]
    assert adapter.attempts[0]["adapter_outcome"] == "empty"


@pytest.mark.parametrize("failure,kind", [
    (TimeoutError("synthetic timeout"), "OllamaTimeoutError"),
    (URLError(TimeoutError("synthetic wrapped timeout")), "OllamaTimeoutError"),
    (URLError(ConnectionRefusedError("synthetic refusal")), "OllamaRequestError"),
    (ConnectionResetError("synthetic reset"), "OllamaRequestError"),
    (HTTPError("http://127.0.0.1:11434/api/generate", 500, "synthetic", {}, None), "OllamaRequestError"),
])
def test_request_failure_retains_previous_results_and_allows_retry(http, prepared, failure, kind):
    adapter = backend.OllamaOCRAdapter()
    run_parser(prepared, adapter)
    before = record(prepared)
    http.response = failure
    assert run_parser(prepared, adapter, force=True)["failed_pages"] == [1]
    after = record(prepared)
    assert after["page_text"] == before["page_text"] and after["parser_provenance"] == before["parser_provenance"]
    assert after["errors"][0]["error_type"] == kind
    assert adapter.attempts[-1]["error_type"] == kind and adapter.attempts[-1]["adapter_outcome"] == "failed"
    http.response = response_text()
    assert run_parser(prepared, adapter)["audit"]["ok"]
    assert len(http.generations) == 3


@pytest.mark.parametrize("damage", ["bad_json", "utf8", "not_object", "server_error", "missing_text",
                                    "wrong_text_type", "not_done", "length", "wrong_model", "negative_metric",
                                    "bool_metric", "nonfinite"])
def test_abnormal_responses_never_mark_success(http, prepared, damage):
    payload = response_text()
    if damage == "bad_json": payload = b"not json"
    elif damage == "utf8": payload = b"\xff"
    elif damage == "not_object": payload = []
    elif damage == "server_error": payload = {"error": "Synthetic failure"}
    elif damage == "missing_text": del payload["response"]
    elif damage == "wrong_text_type": payload["response"] = 123
    elif damage == "not_done": payload["done"] = False
    elif damage == "length": payload["done_reason"] = "length"
    elif damage == "wrong_model": payload["model"] = "wrong-model"
    elif damage == "negative_metric": payload["eval_duration"] = -1
    elif damage == "bool_metric": payload["eval_duration"] = True
    else: payload["eval_duration"] = float("nan")
    http.response = payload
    adapter = backend.OllamaOCRAdapter()
    assert run_parser(prepared, adapter)["failed_pages"] == [1]
    assert record(prepared)["ocr_status"] == "failed"
    assert record(prepared)["errors"][0]["error_type"] == "OllamaResponseError"


def test_optional_metrics_can_be_absent(http, prepared):
    http.response = {"model": backend.DEFAULT_MODEL, "response": TEXT, "done": True}
    adapter = backend.OllamaOCRAdapter()
    assert run_parser(prepared, adapter)["audit"]["ok"]
    assert all(value is None for value in adapter.attempts[0]["metrics"].values())


def test_repeat_skips_http_and_layout_is_untouched(http, prepared):
    run_parser(prepared, SyntheticAdapter(("layout",), layout_result, "synthetic/layout"))
    before = record(prepared)
    adapter = backend.OllamaOCRAdapter()
    run_parser(prepared, adapter)
    index = (prepared / "pages.jsonl").read_bytes()
    assert run_parser(prepared, adapter)["skipped_pages"] == [{"pdf_page": 1, "reason": "already_parsed"}]
    assert len(http.generations) == len(adapter.attempts) == 1
    assert (prepared / "pages.jsonl").read_bytes() == index
    for field in ("layout_regions", "image_regions", "relations", "layout_status"):
        assert record(prepared)[field] == before[field]
    assert record(prepared)["parser_provenance"]["layout"] == before["parser_provenance"]["layout"]


@pytest.mark.parametrize("change", ["model_digest", "server_version", "prompt", "options", "timeout"])
def test_changed_identity_invalidates_ocr_cache(http, prepared, change):
    original = backend.OllamaOCRAdapter()
    run_parser(prepared, original)
    kwargs = {}
    if change == "model_digest": http.tags["models"][0]["digest"] = "b" * 64
    elif change == "server_version": http.version["version"] = "new-test-version"
    elif change == "prompt": kwargs["prompt"] = "Synthetic alternative prompt"
    elif change == "options": kwargs["options"] = {"num_predict": 1234}
    else: kwargs["timeout"] = 42
    updated = backend.OllamaOCRAdapter(**kwargs)
    assert updated.name != original.name
    assert run_parser(prepared, updated)["parsed_pages"] == [1]
    assert len(http.generations) == 2 and record(prepared)["page_text"]["text"] == TEXT


@pytest.mark.parametrize("kwargs", [
    {"base_url": "https://example.com"}, {"base_url": "http://127.0.0.1:11434/path"},
    {"timeout": 0}, {"timeout": float("inf")}, {"timeout": True},
    {"options": {"temperature": float("nan")}}, {"options": {"num_ctx": 0}},
    {"empty_marker": "<NO_TEXT>"},
])
def test_invalid_configuration_rejected_before_network(http, kwargs):
    with pytest.raises(ValueError):
        backend.OllamaOCRAdapter(**kwargs)
    assert http.requests == []


@pytest.mark.parametrize("damage", ["missing_model", "missing_version", "invalid_digest"])
def test_metadata_failure_never_downloads_or_calls_generate(http, damage):
    if damage == "missing_model": http.tags["models"] = []
    elif damage == "missing_version": http.version = {}
    else: http.tags["models"][0]["digest"] = "bad"
    with pytest.raises(backend.OllamaResponseError):
        backend.OllamaOCRAdapter()
    assert http.generations == []
    assert not any("pull" in req.full_url for req, _ in http.requests)


def test_redirects_are_disabled():
    with pytest.raises(backend.OllamaRequestError, match="redirects"):
        backend._NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.com")


def test_interrupt_keeps_index_and_records_incomplete_attempt(http, prepared):
    http.response = KeyboardInterrupt()
    adapter = backend.OllamaOCRAdapter()
    before = (prepared / "pages.jsonl").read_bytes()
    with pytest.raises(KeyboardInterrupt):
        run_parser(prepared, adapter)
    assert (prepared / "pages.jsonl").read_bytes() == before
    assert adapter.attempts[0]["adapter_outcome"] == "interrupted"


def test_script_saves_private_telemetry_without_text_on_stdout(http, prepared, tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("run_ollama_script", Path("scripts/run_ollama_ocr.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "scripts/run_ollama_ocr.py"))
    assert module.main([str(prepared), "--pages", "1"]) == 0
    stdout = capsys.readouterr().out
    assert TEXT not in stdout and "Synthetic HTTP fixture text" not in stdout
    summary = json.loads(stdout)
    log = json.loads(Path(summary["log_path"]).read_text())
    assert log["attempts"][0]["response_text"] == TEXT
    assert log["attempts"][0]["persisted_ocr_status"] == "success"
    assert log["identity"]["model_digest"] == "a" * 64
    assert module.main([str(prepared), "--pages", "1"]) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["attempts"] == []
    assert repeated["skipped_pages"] == [{"pdf_page": 1, "reason": "already_parsed"}]
    assert len(http.generations) == 1
    with pytest.raises(SystemExit) as exc:
        module.main([str(prepared)])
    assert exc.value.code == 2


def test_script_unavailable_service_records_failed_setup(http, prepared, tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("run_ollama_unavailable", Path("scripts/run_ollama_ocr.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "scripts/run_ollama_ocr.py"))
    http.version = URLError(ConnectionRefusedError("synthetic unavailable service"))
    before = (prepared / "pages.jsonl").read_bytes()
    assert module.main([str(prepared), "--pages", "1"]) == 1
    summary = json.loads(capsys.readouterr().out)
    log = json.loads(Path(summary["log_path"]).read_text())
    assert log["identity"] is None and log["attempts"] == []
    assert log["requested_model"] == backend.DEFAULT_MODEL and log["error"]
    assert (prepared / "pages.jsonl").read_bytes() == before
    assert http.generations == []
