# Naymark Image Processing API — Project Plan

**Status**: Approved
**Created**: 2026-11-19
**Mode**: New project

## 1. Project Overview

Build a public, unauthenticated HTTP API that accepts an image, applies the `naymark` package to remove its supported visible watermark, and synchronously returns the processed image. Preserve Cloudflare as the edge/deployment platform and GitHub as the source and deployment trigger.

The service is specifically for supported visible watermark templates in Gemini-generated images that the caller created or is authorized to modify. Reconstruction is approximate. This is not a general-purpose watermark remover and must not target invisible provenance signals.

## 2. Requirements & Scope

- Backend: Python with FastAPI; use Uvicorn in a conventional container.
- Request flow: upload one image → validate → process in memory → return the processed image in the same response.
- Public API: no caller sign-in. Abuse controls are mandatory because access is public.
- Use `naymark` 0.1.0 (alpha, MIT, Python >=3.9; classifiers through 3.13), with `numpy>=1.23` and `opencv-python-headless>=4.8`. Pin and review dependencies and benchmark the alpha package before production.
- Use the library defaults: threshold `0.6`, maximum `5` watermarks. Keep these server-side initially; expose overrides only after product and abuse review.
- Do not retain source or processed images. No database or object store is required for the synchronous, in-memory flow. Consider Cloudflare R2 only if a validated deployment constraint requires temporary object storage; use short lifecycle expiration and delete objects on completion/failure.
- Provisional upload cap: 10 MiB, configurable and subject to validation against supported formats, processing time, and platform limits.

## 3. Architecture & Deployment

1. **Cloudflare edge:** Cloudflare Worker as the public API gateway, with WAF/DDoS protections, request-size checks, per-client rate limiting, and narrowly scoped cache rules. It forwards permitted requests to the Python origin.
2. **Python origin:** FastAPI/Uvicorn in a container. Conventional FastAPI/Uvicorn containers are not directly deployable as Cloudflare Workers. First validate Cloudflare Containers availability for the account/region, Python base-image and native OpenCV compatibility, resource limits, scaling, and request/response behavior. If all checks pass, run the origin in Cloudflare Containers behind the Worker.
3. **Fallback preserving Cloudflare:** If Cloudflare Containers cannot meet those requirements, run the Python container on a suitable external container platform and keep Cloudflare Worker, WAF, DDoS controls, and DNS/proxy at the front. Document the origin provider decision before production; do not represent this fallback as a Worker-native FastAPI deployment.
4. **Delivery:** GitHub Actions builds and tests the container, then deploys Cloudflare resources/origin through Cloudflare APIs using GitHub OIDC/federated credentials where supported. Do not store long-lived deployment secrets in source control. Protect production deployment with branch/environment approvals.

## 4. API & Processing Flow

- `POST /v1/process`: accept a single multipart image upload. Validate declared media type, file signature, successful decode, dimensions/pixel count, and configured byte limit before processing. Reject unsupported, malformed, or oversized inputs with a clear 4xx response.
- Process decoded image data in memory using `remove_watermark`; do not write user image data to logs, disk, or durable storage. Bound processing time and output size. Return the resulting image synchronously as `image/png` with `Cache-Control: private, no-store`; use a request/correlation ID in response headers.
- `GET /healthz`: lightweight liveness check. `GET /readyz`: readiness check for application/dependency initialization; expose no secrets or infrastructure details.
- Document status codes and a generic error body. Do not echo uploaded content or sensitive decoder details.

## 5. Traffic, Reliability & Operations

- Apply Cloudflare WAF and managed DDoS protections, per-IP token-bucket rate limits, burst limits, and request-body limits at the edge; enforce independent limits at the origin so direct-origin access cannot bypass controls.
- Restrict origin ingress to the edge where practical, and validate an origin-to-edge authentication mechanism or equivalent network control. No end-user authentication is planned.
- Use bounded concurrency/admission control, finite request and processing timeouts, memory limits, backpressure, and graceful overload responses (for example 429/503). Do not queue jobs: processing remains synchronous unless asynchronous behavior is separately approved.
- Size limits must also cover decoded pixel dimensions to prevent decompression/resource exhaustion. Benchmark representative images and tune rate, concurrency, and time limits before launch.
- Cache only safe public/static assets and non-sensitive health or metadata responses where appropriate. Never cache uploads or processed output. Do not log request bodies, image bytes, or sensitive headers; emit aggregate latency, rejection, error, and resource metrics.
- Validate Cloudflare platform limits and end-to-end behavior in staging, including large/invalid files, concurrent requests, cold starts, and origin failure.

## 6. Design System & UI

**Component Library**: None — this is a backend-only API with no user interface.

## 7. Data & Storage

**Data Stores**: No datastore required for the planned synchronous flow. Handle source and result images in memory and discard them after the response. Cloudflare R2 is an optional, conditional design only if platform constraints require temporary object storage; it is not part of the baseline and must not become indefinite image retention.

## 8. Security, Privacy & Safety

- Treat all uploads as untrusted. Validate size, actual file type, decodability, dimensions, and processing budget; reject content outside the supported image scope.
- Use least-privilege deployment identities, GitHub OIDC/federation where available, protected deployment environments, dependency pinning, and vulnerability review.
- Keep the API unauthenticated as requested; mitigate abuse through edge and origin limits rather than implying authentication.
- State the supported use case and approximate result behavior in API documentation. Process only images the caller created or is authorized to modify. Do not attempt to remove invisible provenance signals.
- Set no-store response behavior for image output and avoid persistent image retention, diagnostic dumps, or image-bearing telemetry.

## 9. Delivery & Acceptance Criteria

- A GitHub-based, repeatable build/test/deploy pipeline uses federated deployment credentials where supported.
- The selected Cloudflare/origin deployment path is validated for Python, FastAPI/Uvicorn, NumPy, and headless OpenCV compatibility, resource limits, and scaling before production.
- Valid supported uploads return a processed image synchronously; malformed, unsupported, oversized, rate-limited, or overloaded requests fail safely and predictably.
- Automated tests cover endpoint behavior, validation, no-store headers, processing errors, and concurrency/admission limits.
- Load and abuse testing confirms edge and origin limits, bounded resource use, and graceful behavior without retaining image content.
- Production monitoring reports aggregate service health and performance without logging image data.
