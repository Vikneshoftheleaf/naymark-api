import asyncio
import io
import logging

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from naymark import remove_watermark
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request

app = FastAPI(title="Naymark API", version="0.1.0")
logger = logging.getLogger("naymark_api")

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
MAX_IMAGE_DIMENSION = 8_000

IMAGE_FORMATS = {
    "image/jpeg": ("JPEG", b"\xff\xd8\xff"),
    "image/png": ("PNG", b"\x89PNG\r\n\x1a\n"),
    "image/webp": ("WEBP", b"RIFF"),
}


class InvalidImageError(Exception):
    pass


@app.middleware("http")
async def disable_caching(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    response = await call_next(request)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def process_image(payload: bytes, content_type: str) -> bytes:
    image_format, signature = IMAGE_FORMATS[content_type]
    if not payload.startswith(signature) or (
        content_type == "image/webp" and payload[8:12] != b"WEBP"
    ):
        raise InvalidImageError

    try:
        with Image.open(io.BytesIO(payload)) as source:
            width, height = source.size
            if (
                width <= 0
                or height <= 0
                or width > MAX_IMAGE_DIMENSION
                or height > MAX_IMAGE_DIMENSION
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise HTTPException(status_code=413, detail="Image dimensions exceed the limit.")
            if source.format != image_format:
                raise InvalidImageError
            source.verify()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidImageError from exc

    decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise InvalidImageError

    result = remove_watermark(decoded, threshold=0.6, max_watermarks=5)
    processed = getattr(result, "image", result)
    if not isinstance(processed, np.ndarray):
        processed = np.asarray(processed)
    if (
        processed.dtype != np.uint8
        or processed.ndim != 3
        or processed.shape[2] != 3
        or processed.shape[:2] != decoded.shape[:2]
    ):
        raise ValueError("Naymark returned an unsupported image.")

    success, encoded = cv2.imencode(".png", processed)
    if not success:
        raise ValueError("Could not encode the processed image.")
    output = encoded.tobytes()
    if len(output) > MAX_OUTPUT_BYTES:
        raise HTTPException(status_code=422, detail="Processed image exceeds the response limit.")
    return output


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/process")
async def process_upload(file: UploadFile = File(...)) -> Response:
    if file.content_type not in IMAGE_FORMATS:
        raise HTTPException(status_code=415, detail="Upload a JPEG, PNG, or WebP image.")

    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded image is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 4 MiB upload limit.")

    try:
        output = await asyncio.to_thread(process_image, payload, file.content_type)
    except InvalidImageError as exc:
        raise HTTPException(status_code=400, detail="The uploaded image is invalid.") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Image processing failed")
        raise HTTPException(status_code=422, detail="Image processing failed.") from exc

    return Response(
        content=output,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )
