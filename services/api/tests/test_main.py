import io
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_rate_state():
    main_module._rate_buckets.clear()
    yield
    main_module._rate_buckets.clear()


def image_bytes(size=(32, 32), image_format="PNG") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (128, 128, 128)).save(output, format=image_format)
    return output.getvalue()


def upload(data=None, media_type="image/png", name="image.png"):
    return client.post(
        "/v1/process",
        files={"file": (name, image_bytes() if data is None else data, media_type)},
    )


def test_health_and_readiness_are_non_cacheable_and_correlated():
    for path, expected in (("/healthz", {"status": "ok"}), ("/readyz", {"status": "ready"})):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == expected
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-request-id"]


def test_rejects_unsupported_media_type():
    response = upload(b"hello", "text/plain", "test.txt")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"
    assert response.headers["cache-control"] == "private, no-store"


def test_rejects_signature_mismatch_and_malformed_image():
    response = upload(b"not an image")
    assert response.status_code == 415
    malformed_png = b"\x89PNG\r\n\x1a\n" + b"bad"
    response = upload(malformed_png)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_image"


def test_rejects_extra_multipart_parts():
    response = client.post(
        "/v1/process",
        files=[
            ("file", ("one.png", image_bytes(), "image/png")),
            ("extra", ("two.png", image_bytes(), "image/png")),
        ],
    )
    assert response.status_code == 400


def test_rejects_oversized_file(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "MAX_UPLOAD_BYTES", 16)
    response = upload()
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_rejects_excessive_decoded_dimensions(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "MAX_PIXELS", 100)
    response = upload()
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "image_dimensions_too_large"


def test_authentication_is_required_when_origin_secret_is_configured(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "ORIGIN_AUTH_TOKEN", "test-secret")
    response = client.get("/healthz")
    assert response.status_code == 403
    assert response.headers["cache-control"] == "private, no-store"
    response = client.get("/healthz", headers={"X-Origin-Auth": "test-secret"})
    assert response.status_code == 200


def test_origin_rate_limit_is_per_client_and_returns_retry_after(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "ORIGIN_RATE_LIMIT", 1)
    monkeypatch.setattr(main_module, "_RATE_BUCKET_CAPACITY", 1)
    main_module._rate_buckets.clear()
    first = client.post("/v1/process", files={"file": ("x.png", b"", "image/png")})
    second = client.post("/v1/process", files={"file": ("x.png", b"", "image/png")})
    assert first.status_code == 400
    assert second.status_code == 429
    assert second.headers["retry-after"] == "5"


def test_processes_valid_image_in_memory_and_sets_no_store(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "remove_supported_watermark", lambda image: np.asarray(image))
    main_module._rate_buckets.clear()
    response = upload()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-request-id"]
    with Image.open(io.BytesIO(response.content)) as result:
        assert result.size == (32, 32)


def test_actual_naymark_package_path_returns_png():
    response = upload()
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.content)) as result:
        assert result.format == "PNG"
        assert result.size == (32, 32)


def test_admission_control_rejects_concurrent_processing(monkeypatch):
    import app.main as main_module

    started = threading.Event()
    release = threading.Event()

    def blocked(_payload, _declared_type):
        started.set()
        release.wait(timeout=2)
        return b"processed"

    monkeypatch.setattr(main_module, "_process_png", blocked)
    main_module._rate_buckets.clear()
    first_response = []
    thread = threading.Thread(target=lambda: first_response.append(upload()))
    thread.start()
    try:
        assert started.wait(timeout=2)
        response = upload()
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "overloaded"
    finally:
        release.set()
        thread.join(timeout=3)
    assert first_response and first_response[0].status_code == 200


def test_processing_timeout_returns_a_safe_error(monkeypatch):
    import app.main as main_module

    def slow(_payload, _declared_type):
        time.sleep(0.05)
        return b"processed"

    monkeypatch.setattr(main_module, "_process_png", slow)
    monkeypatch.setattr(main_module, "REQUEST_TIMEOUT_SECONDS", 0.001)
    response = upload()
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "processing_timeout"
    assert response.headers["cache-control"] == "private, no-store"
