import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app as api

client = TestClient(api.app)


def png_image(size: tuple[int, int] = (32, 32)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (128, 128, 128)).save(output, format="PNG")
    return output.getvalue()


def test_health_check():
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_rejects_unsupported_content_type():
    response = client.post(
        "/v1/process",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 415


def test_rejects_oversized_upload(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 8)
    response = client.post(
        "/v1/process",
        files={"file": ("image.png", b"x" * 9, "image/png")},
    )

    assert response.status_code == 413


def test_rejects_malformed_image():
    response = client.post(
        "/v1/process",
        files={"file": ("image.png", b"not an image", "image/png")},
    )

    assert response.status_code == 400


def test_returns_processed_png_without_caching(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api, "remove_watermark", lambda image, **kwargs: image)
    response = client.post(
        "/v1/process",
        files={"file": ("image.png", png_image(), "image/png")},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    with Image.open(io.BytesIO(response.content)) as result:
        assert result.size == (32, 32)


def test_uses_naymark_package():
    response = client.post(
        "/v1/process",
        files={"file": ("image.png", png_image(), "image/png")},
    )

    assert response.status_code == 200
    with Image.open(io.BytesIO(response.content)) as result:
        assert result.format == "PNG"
