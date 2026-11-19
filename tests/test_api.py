import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import app as api

client = TestClient(api.app)


def png_image(size: tuple[int, int] = (32, 32)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (128, 128, 128)).save(output, format="PNG")
    return output.getvalue()


def test_accepts_image_format_outside_original_allowlist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api, "remove_watermark", lambda image, **kwargs: image)
    output = io.BytesIO()
    Image.new("RGB", (32, 32), (128, 128, 128)).save(output, format="BMP")
    response = client.post(
        "/v1/process",
        files={"file": ("image.bmp", output.getvalue(), "image/bmp")},
    )

    assert response.status_code == 200
    with Image.open(io.BytesIO(response.content)) as result:
        assert result.format == "PNG"


def test_health_check():
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_frontend_is_served_on_home_with_embedded_playground_and_docs():
    home = client.get("/")
    docs = client.get("/docs/")
    docs_without_slash = client.get("/docs")
    stylesheet = client.get("/styles.css")

    assert home.status_code == 200
    assert "Clean up Gemini images." in home.text
    assert 'id="playground"' in home.text
    assert 'id="drop-zone"' in home.text
    assert home.text.count('id="choose-image"') == 1
    assert 'id="preview-modes"' in home.text
    assert 'id="zoom-in"' in home.text
    assert docs.status_code == 200
    assert "Naymark API Docs" in docs.text
    assert docs_without_slash.status_code == 200
    assert stylesheet.status_code == 200
    assert "color-scheme: light" in stylesheet.text


def test_api_can_start_when_public_assets_are_not_bundled(tmp_path):
    api_without_public = FastAPI(docs_url=None, redoc_url=None)

    api.configure_public_routes(api_without_public, tmp_path / "public")

    assert not any(route.path in {"/", "/docs", "/docs/"} for route in api_without_public.routes)


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
