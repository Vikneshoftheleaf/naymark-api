from __future__ import annotations

import asyncio
import hmac
import io
import ipaddress
import logging
import math
import os
import time
import uuid
from collections import OrderedDict
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image, UnidentifiedImageError
from python_multipart.multipart import MultipartParser, parse_options_header

from .naymark import remove_supported_watermark


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


MAX_UPLOAD_BYTES = _positive_int("MAX_UPLOAD_BYTES", 10 * 1024 * 1024)
MAX_MULTIPART_OVERHEAD_BYTES = _positive_int("MAX_MULTIPART_OVERHEAD_BYTES", 64 * 1024)
MAX_PIXELS = _positive_int("MAX_PIXELS", 12_000_000)
MAX_DIMENSION = _positive_int("MAX_DIMENSION", 8_000)
MAX_OUTPUT_BYTES = _positive_int("MAX_OUTPUT_BYTES", 40 * 1024 * 1024)
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "25"))
UPLOAD_TIMEOUT_SECONDS = float(os.getenv("UPLOAD_TIMEOUT_SECONDS", "20"))
MAX_CONCURRENT_REQUESTS = _positive_int("MAX_CONCURRENT_REQUESTS", 1)
ORIGIN_AUTH_TOKEN = os.getenv("ORIGIN_AUTH_TOKEN", "")
ORIGIN_RATE_LIMIT = _positive_int("ORIGIN_RATE_LIMIT", 12)
ORIGIN_RATE_PERIOD_SECONDS = _positive_int("ORIGIN_RATE_PERIOD_SECONDS", 60)

if REQUEST_TIMEOUT_SECONDS <= 0 or UPLOAD_TIMEOUT_SECONDS <= 0:
    raise ValueError("Request timeouts must be positive")
if not math.isfinite(REQUEST_TIMEOUT_SECONDS) or not math.isfinite(UPLOAD_TIMEOUT_SECONDS):
    raise ValueError("Request timeouts must be finite")

app = FastAPI(title="Naymark Image Processing API", version="0.1.0")
admission_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
_rate_buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
_RATE_BUCKET_CAPACITY = max(1, min(ORIGIN_RATE_LIMIT, 3))
logger = logging.getLogger("naymark_api")
logging.getLogger("uvicorn.access").disabled = True

MEDIA_FORMATS = {
    "image/jpeg": ("JPEG", b"\xff\xd8\xff"),
    "image/png": ("PNG", b"\x89PNG\r\n\x1a\n"),
    "image/webp": ("WEBP", b"RIFF"),
}
REQUEST_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


class UploadError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def error(status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers={
            "X-Request-ID": request_id,
            "Cache-Control": "private, no-store",
            **({"Retry-After": "5"} if status_code == 429 else {}),
        },
        content={"error": {"code": code, "message": message, "request_id": request_id}},
    )


def _request_id(request: Request) -> str:
    supplied = request.headers.get("X-Request-ID", "")
    if 1 <= len(supplied) <= 64 and all(char in REQUEST_ID_CHARS for char in supplied):
        return supplied
    return str(uuid.uuid4())


def _origin_authorized(request: Request) -> bool:
    supplied = request.headers.get("X-Origin-Auth", "").encode("utf-8")
    expected = ORIGIN_AUTH_TOKEN.encode("utf-8")
    return hmac.compare_digest(supplied, expected)


def _rate_limit_key(request: Request) -> str:
    # Trust the Worker-supplied address only when the edge-origin secret matched.
    address = ""
    if ORIGIN_AUTH_TOKEN and _origin_authorized(request):
        address = request.headers.get("X-Client-IP", "")
    if not address and request.client is not None:
        address = request.client.host
    try:
        return ipaddress.ip_address(address).compressed
    except ValueError:
        return "unknown"


def _allow_origin_request(key: str, now: float | None = None) -> bool:
    current = time.monotonic() if now is None else now
    refill_per_second = ORIGIN_RATE_LIMIT / ORIGIN_RATE_PERIOD_SECONDS
    tokens, updated = _rate_buckets.pop(key, (_RATE_BUCKET_CAPACITY, current))
    tokens = min(_RATE_BUCKET_CAPACITY, tokens + max(0.0, current - updated) * refill_per_second)
    allowed = tokens >= 1.0
    if allowed:
        tokens -= 1.0
    _rate_buckets[key] = (tokens, current)
    while len(_rate_buckets) > 10_000:
        _rate_buckets.popitem(last=False)
    return allowed


async def _read_limited_body(request: Request, maximum: int) -> bytes:
    chunks: list[bytes] = []
    length = 0
    async for chunk in request.stream():
        length += len(chunk)
        if length > maximum:
            raise UploadError(413, "payload_too_large", "The request exceeds the configured size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _extract_image_part(content_type: str, body: bytes) -> tuple[bytes, str]:
    try:
        media_type, params = parse_options_header(content_type)
    except Exception as exc:
        raise UploadError(400, "invalid_multipart", "A valid multipart image upload is required.") from exc
    boundary = params.get(b"boundary")
    if media_type.lower() != b"multipart/form-data" or not boundary or len(boundary) > 70:
        raise UploadError(400, "invalid_multipart", "A valid multipart image upload is required.")

    parts: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    partial_name = bytearray()
    partial_value = bytearray()

    def on_part_begin() -> None:
        nonlocal current
        if len(parts) >= 1:
            raise ValueError("too_many_parts")
        current = {"headers": {}, "payload": bytearray()}

    def on_header_field(data: bytes, start: int, end: int) -> None:
        partial_name.extend(data[start:end])

    def on_header_value(data: bytes, start: int, end: int) -> None:
        partial_value.extend(data[start:end])

    def on_header_end() -> None:
        if current is None:
            raise ValueError("invalid_headers")
        current["headers"][bytes(partial_name).lower()] = bytes(partial_value).strip()
        partial_name.clear()
        partial_value.clear()

    def on_headers_finished() -> None:
        if current is None:
            raise ValueError("invalid_part")
        disposition, options = parse_options_header(current["headers"].get(b"content-disposition"))
        if disposition.lower() != b"form-data":
            raise ValueError("invalid_disposition")
        current["field_name"] = options.get(b"name", b"").decode("latin-1")
        current["is_file"] = b"filename" in options
        raw_type = current["headers"].get(b"content-type", b"")
        current["content_type"] = raw_type.split(b";", 1)[0].decode("latin-1").strip().lower()

    def on_part_data(data: bytes, start: int, end: int) -> None:
        if current is None:
            raise ValueError("invalid_part")
        payload = current["payload"]
        if len(payload) + end - start > MAX_UPLOAD_BYTES:
            raise UploadError(413, "payload_too_large", "The uploaded image exceeds the size limit.")
        payload.extend(data[start:end])

    def on_part_end() -> None:
        if current is None:
            raise ValueError("invalid_part")
        parts.append(current)

    callbacks = {
        "on_part_begin": on_part_begin,
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished,
        "on_part_data": on_part_data,
        "on_part_end": on_part_end,
    }
    try:
        parser = MultipartParser(boundary, callbacks, max_size=MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES)
        parser.write(body)
        parser.finalize()
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError(400, "invalid_multipart", "A valid multipart image upload is required.") from exc

    if (
        len(parts) != 1
        or parts[0].get("field_name") != "file"
        or not parts[0].get("is_file")
        or not parts[0]["payload"]
    ):
        raise UploadError(400, "invalid_upload", "Provide exactly one non-empty image in the 'file' field.")
    declared_type = parts[0].get("content_type", "")
    if declared_type not in MEDIA_FORMATS:
        raise UploadError(415, "unsupported_media_type", "Upload a JPEG, PNG, or WebP image.")
    return bytes(parts[0]["payload"]), declared_type


def _validate_image(payload: bytes, declared_type: str) -> None:
    image_format, signature = MEDIA_FORMATS[declared_type]
    if not payload.startswith(signature) or (
        declared_type == "image/webp" and payload[8:12] != b"WEBP"
    ):
        raise UploadError(415, "unsupported_media_type", "The file signature is not a supported image.")
    try:
        with Image.open(io.BytesIO(payload)) as source:
            width, height = source.size
            if (
                width <= 0
                or height <= 0
                or width > MAX_DIMENSION
                or height > MAX_DIMENSION
                or width * height > MAX_PIXELS
            ):
                raise UploadError(413, "image_dimensions_too_large", "The image exceeds configured dimension limits.")
            if source.format != image_format:
                raise UploadError(415, "unsupported_media_type", "The file format does not match its media type.")
            source.verify()
    except UploadError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise UploadError(400, "invalid_image", "The image is malformed or could not be decoded.") from exc


def _process_png(payload: bytes, declared_type: str) -> bytes:
    _validate_image(payload, declared_type)
    decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise ValueError("decode_failed")
    processed = remove_supported_watermark(decoded)
    if not isinstance(processed, np.ndarray):
        processed = np.asarray(processed)
    if (
        processed.dtype != np.uint8
        or processed.ndim != 3
        or processed.shape[2] != 3
        or processed.shape[:2] != decoded.shape[:2]
    ):
        raise ValueError("invalid_processing_result")
    ok, encoded = cv2.imencode(".png", processed, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise ValueError("encode_failed")
    output = encoded.tobytes()
    if len(output) > MAX_OUTPUT_BYTES:
        raise ValueError("output_too_large")
    return output


def _release_when_done(task: asyncio.Task[bytes], semaphore: asyncio.Semaphore) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass
    semaphore.release()


@app.middleware("http")
async def request_context(request: Request, call_next: Any) -> Response:
    started_at = time.perf_counter()
    request_id = _request_id(request)
    request.state.request_id = request_id
    if ORIGIN_AUTH_TOKEN and not _origin_authorized(request):
        response: Response = error(403, "forbidden", "Forbidden.", request_id)
    elif request.url.path == "/v1/process" and not _allow_origin_request(_rate_limit_key(request)):
        response = error(429, "rate_limited", "Too many requests. Please retry later.", request_id)
    else:
        try:
            response = await call_next(request)
        except Exception:
            response = error(500, "internal_error", "The request could not be completed.", request_id)
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "private, no-store"
    if request.url.path in {"/v1/process", "/healthz", "/readyz"}:
        logger.info(
            "request_complete route=%s status=%d duration_ms=%.1f",
            request.url.path,
            response.status_code,
            (time.perf_counter() - started_at) * 1000,
        )
    return response


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/v1/process")
async def process_image(request: Request) -> Response:
    request_id = request.state.request_id
    content_length = request.headers.get("content-length")
    total_limit = MAX_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES
    if content_length:
        try:
            if int(content_length) > total_limit or int(content_length) < 0:
                return error(413, "payload_too_large", "The request exceeds the configured size limit.", request_id)
        except ValueError:
            return error(400, "invalid_request", "The request could not be parsed.", request_id)

    try:
        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise UploadError(415, "unsupported_media_type", "Submit an image using multipart/form-data.")
        body = await asyncio.wait_for(_read_limited_body(request, total_limit), timeout=UPLOAD_TIMEOUT_SECONDS)
        payload, declared_type = _extract_image_part(content_type, body)
    except UploadError as exc:
        return error(exc.status_code, exc.code, exc.message, request_id)
    except asyncio.TimeoutError:
        return error(408, "upload_timeout", "The upload took too long.", request_id)
    except Exception:
        return error(400, "invalid_upload", "The image upload could not be read.", request_id)

    if admission_semaphore.locked():
        return error(503, "overloaded", "The service is temporarily overloaded.", request_id)
    await admission_semaphore.acquire()
    semaphore = admission_semaphore
    processing = asyncio.create_task(asyncio.to_thread(_process_png, payload, declared_type))
    release_here = True
    try:
        encoded = await asyncio.wait_for(asyncio.shield(processing), timeout=REQUEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        release_here = False
        processing.add_done_callback(lambda task: _release_when_done(task, semaphore))
        return error(504, "processing_timeout", "Image processing timed out.", request_id)
    except asyncio.CancelledError:
        release_here = False
        processing.add_done_callback(lambda task: _release_when_done(task, semaphore))
        raise
    except UploadError as exc:
        return error(exc.status_code, exc.code, exc.message, request_id)
    except Exception:
        return error(422, "processing_failed", "The image could not be processed.", request_id)
    finally:
        if release_here:
            semaphore.release()

    return Response(
        content=encoded,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store", "X-Request-ID": request_id},
    )
