import asyncio
import io
import logging
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from PIL import Image, UnidentifiedImageError
from naymark import remove_watermark
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.staticfiles import StaticFiles

PUBLIC_DIR = Path(__file__).parent / "public"

app = FastAPI(title="Naymark API", version="0.1.0", docs_url=None, redoc_url=None)
logger = logging.getLogger("naymark_api")

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
MAX_IMAGE_DIMENSION = 8_000

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


def process_image(payload: bytes) -> bytes:
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
            source.verify()
        with Image.open(io.BytesIO(payload)) as source:
            rgb_image = np.asarray(source.convert("RGB"))
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidImageError from exc

    decoded = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR)

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


def configure_public_routes(application: FastAPI, public_dir: Path) -> None:
    # Vercel serves public assets separately and omits them from the Python function bundle.
    if not public_dir.is_dir():
        return

    @application.get("/", include_in_schema=False)
    async def homepage() -> FileResponse:
        return FileResponse(public_dir / "index.html", media_type="text/html")

    @application.get("/docs", include_in_schema=False)
    @application.get("/docs/", include_in_schema=False)
    async def documentation() -> FileResponse:
        return FileResponse(public_dir / "docs" / "index.html", media_type="text/html")

    application.mount("/", StaticFiles(directory=public_dir, html=True), name="public")


@app.post("/v1/process")
async def process_upload(file: UploadFile = File(...)) -> Response:
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=415, detail="Upload an image file.")

    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded image is empty.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 4 MiB upload limit.")

    try:
        output = await asyncio.to_thread(process_image, payload)
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


configure_public_routes(app, PUBLIC_DIR)
