"""Local Ollama OCR via the standard library; no model download or bbox invention."""

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ..adapters import ParserResult
from ..models import PageRecord, PageText

DEFAULT_MODEL = "openbmb/minicpm-ocr1.0:f16"
DEFAULT_PROMPT = "Text Recognition:"
METRIC_FIELDS = ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration",
                 "prompt_eval_count", "eval_count", "prompt_eval_cached_count")


class OllamaRequestError(RuntimeError):
    """Local service unavailable, HTTP failure, or unsafe redirect."""


class OllamaTimeoutError(TimeoutError):
    """Socket timeout; the server may still be processing the request."""


class OllamaResponseError(ValueError):
    """Malformed, incomplete, truncated or ambiguously empty response."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise OllamaRequestError("Ollama HTTP redirects are disabled")


def _reject_constant(value):
    raise OllamaResponseError(f"Non-finite JSON constant: {value}")


class OllamaOCRAdapter:
    """OCR-only adapter bound to a locally installed tag/digest and configuration.

    Construction reads /api/version and /api/tags, but never loads/downloads a
    model. Calls are sequential. Do not update the model tag during a run.
    ``attempts`` contains private response text and timing evidence, not a public
    log. The runner remains the sole writer of page results.
    """

    stages = ("ocr",)

    def __init__(self, *, model: str = DEFAULT_MODEL,
                 base_url: str = "http://127.0.0.1:11434", timeout: float = 180,
                 prompt: str = DEFAULT_PROMPT, options: dict | None = None,
                 keep_alive: str = "5m", empty_marker: str | None = None):
        url = urlsplit(base_url)
        if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1")
                or url.username or url.password or url.path not in ("", "/")
                or url.query or url.fragment):
            raise ValueError("Ollama base_url must be an HTTP loopback origin, without credentials/path")
        _ = url.port  # validate malformed ports before any network operation
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number of seconds")
        timeout = float(timeout)  # Python and argparse callers share the same cache identity
        for name, value in (("model", model), ("prompt", prompt), ("keep_alive", keep_alive)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonempty string")
        if empty_marker is not None and (
            not isinstance(empty_marker, str) or not empty_marker.strip()
            or empty_marker != empty_marker.strip() or empty_marker not in prompt
        ):
            raise ValueError("empty_marker must be nonempty, trimmed and explicitly present in the prompt")
        configured = {"temperature": 0, "num_ctx": 16384, "num_predict": 8192}
        if options is not None:
            if not isinstance(options, dict) or not all(isinstance(k, str) for k in options):
                raise ValueError("options must be an object with string keys")
            configured.update(deepcopy(options))
        json.dumps(configured, allow_nan=False)
        for key in ("num_ctx", "num_predict"):
            if type(configured[key]) is not int or configured[key] <= 0:
                raise ValueError(f"{key} must be a positive integer")
        if (type(configured["temperature"]) not in (int, float)
                or not math.isfinite(configured["temperature"]) or configured["temperature"] < 0):
            raise ValueError("temperature must be a nonnegative finite number")
        self._base_url, self._timeout = base_url.rstrip("/"), timeout
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())
        version = self._request("/api/version").get("version")
        if not isinstance(version, str) or not version.strip():
            raise OllamaResponseError("Missing Ollama server version")
        models = self._request("/api/tags").get("models")
        if not isinstance(models, list) or not all(isinstance(m, dict) for m in models):
            raise OllamaResponseError("Invalid Ollama model inventory")
        matches = [m for m in models if m.get("name", m.get("model")) == model]
        if len(matches) != 1:
            raise OllamaResponseError(f"Expected exactly one locally installed model tag: {model}; no pull attempted")
        installed = matches[0]
        digest = installed.get("digest")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise OllamaResponseError("Missing/invalid model digest in Ollama inventory")
        self._identity = {
            "backend": "ollama-ocr", "backend_version": 1, "base_url": self._base_url,
            "model": model, "model_digest": digest, "ollama_version": version,
            "prompt": prompt, "options": configured, "keep_alive": keep_alive,
            "empty_marker": empty_marker, "timeout_seconds": timeout,
            "stream": False, "raw": False,
        }
        fingerprint = hashlib.sha256(json.dumps(
            self._identity, sort_keys=True, ensure_ascii=False, allow_nan=False
        ).encode()).hexdigest()
        self.name = f"ollama-ocr/v1:{model}@{digest};server={version};config={fingerprint}"
        self.attempts: list[dict] = []

    @property
    def identity(self) -> dict:
        return deepcopy(self._identity)

    def _request(self, endpoint: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body, allow_nan=False).encode("utf-8")
        request = Request(self._base_url + endpoint, data=data,
                          headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            # Do not echo HTTP bodies (they may contain private source text).
            raise OllamaRequestError(f"Ollama HTTP {exc.code} at {endpoint}") from exc
        except TimeoutError as exc:
            raise OllamaTimeoutError(f"Ollama socket timeout after {self._timeout}s at {endpoint}") from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise OllamaTimeoutError(f"Ollama socket timeout after {self._timeout}s at {endpoint}") from exc
            raise OllamaRequestError(f"Cannot reach local Ollama at {endpoint}: {exc.reason}") from exc
        except OSError as exc:
            raise OllamaRequestError(f"Ollama I/O failure at {endpoint}: {type(exc).__name__}") from exc
        try:
            value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeError, ValueError) as exc:
            raise OllamaResponseError(f"Invalid Ollama JSON at {endpoint}") from exc
        if not isinstance(value, dict) or "error" in value:
            raise OllamaResponseError(f"Invalid/error Ollama response at {endpoint}")
        return value

    def parse(self, image_path: Path, page: PageRecord) -> ParserResult:
        started = time.perf_counter()
        attempt = {"document_id": page.document_id, "pdf_page": page.pdf_page,
                   "image_sha256": page.page_image_sha256,
                   "started_at": datetime.now(timezone.utc).isoformat(),
                   "adapter_outcome": "failed", "character_count": None,
                   "metrics": {}, "response_text": None, "structured_bbox_returned": None}
        try:
            config = self._identity
            response = self._request("/api/generate", {
                "model": config["model"], "prompt": config["prompt"],
                "images": [base64.b64encode(Path(image_path).read_bytes()).decode("ascii")],
                "stream": False, "raw": False, "options": config["options"],
                "keep_alive": config["keep_alive"],
            })
            text = response.get("response")
            attempt["response_model"] = response.get("model")
            attempt["done_reason"] = response.get("done_reason")
            attempt["structured_bbox_returned"] = any(
                key in response for key in ("bbox", "bboxes", "boxes", "regions"))
            if isinstance(text, str):
                attempt["response_text"], attempt["character_count"] = text, len(text)
            for field in METRIC_FIELDS:
                value = response.get(field)
                if value is not None and (type(value) is not int or value < 0):
                    raise OllamaResponseError(f"Invalid Ollama metric: {field}")
                attempt["metrics"][field] = value
            if response.get("model") != config["model"]:
                raise OllamaResponseError("Ollama response model differs from requested model")
            if response.get("done") is not True or response.get("done_reason") not in (None, "stop"):
                raise OllamaResponseError("Ollama generation incomplete/truncated or unexpected stop reason")
            if not isinstance(text, str) or not text.strip():
                raise OllamaResponseError("Empty/missing OCR text is not proof of a text-free page")
            if config["empty_marker"] is not None and text.strip() == config["empty_marker"]:
                attempt["adapter_outcome"] = "empty"
                return ParserResult(ocr_status="success", ocr_empty_reason=(
                    f"Ollama completed generation and returned the configured no-text marker "
                    f"{config['empty_marker']!r}; model assertion, not human confirmation"))
            attempt["adapter_outcome"] = "success"
            return ParserResult(ocr_status="success", page_text=PageText(text=text, source=self.name))
        except KeyboardInterrupt:
            attempt["adapter_outcome"], attempt["error_type"] = "interrupted", "KeyboardInterrupt"
            raise
        except Exception as exc:
            attempt["error_type"], attempt["error"] = type(exc).__name__, str(exc)
            raise
        finally:
            attempt["elapsed_seconds"] = time.perf_counter() - started
            self.attempts.append(attempt)
