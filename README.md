# Naymark Image Processing API

A public, unauthenticated FastAPI service that synchronously processes one image with `naymark==0.1.0`. It is intended only for supported visible-watermark templates in Gemini-generated images that the caller created or is authorized to modify. Results are approximate. This is not a general watermark remover and does not target invisible provenance signals.

Uploads and results are processed in memory and are not intentionally written to disk, stored, or logged. Image responses and API errors use `Cache-Control: private, no-store`.

## API

| Method and path | Behavior |
|---|---|
| `POST /v1/process` | Multipart form with exactly one `file` field; returns processed `image/png` |
| `GET /healthz` | Liveness |
| `GET /readyz` | Readiness |

JPEG, PNG, and WebP are accepted. Defaults are 10 MiB per file, 64 KiB multipart overhead, 8,000 pixels per dimension, 12 megapixels, 40 MiB output, one processing slot per container, and a 25-second processing deadline. `naymark` is called with its defaults (`threshold=0.6`, `max_watermarks=5`).

The Worker applies an approximate Cloudflare per-IP rate limit of 6 processing requests per minute per Cloudflare location. The origin independently applies a small in-memory per-IP token bucket (12/minute, burst 3) and bounded processing concurrency. These limits are abuse controls, not a service-level guarantee; benchmark and tune them before launch.

Errors use a generic JSON body:

```json
{"error":{"code":"invalid_image","message":"The image is malformed or could not be decoded.","request_id":"..."}}
```

`400` malformed request/image; `408` upload timeout; `413` upload/dimension limit; `415` unsupported type; `422` processing failure; `429` rate limit; `503` overload/origin failure; `504` processing timeout. Responses include `X-Request-ID`.

Example:

```bash
curl -X POST https://YOUR_API_HOST/v1/process \
  -F "file=@authorized-image.png;type=image/png" \
  -o processed.png
```

## Local API development

```bash
python -m venv .venv
# Activate .venv for your shell, then:
python -m pip install -c requirements.lock -e ".[test]"
uvicorn app.main:app --app-dir services/api --host 127.0.0.1 --port 8080
python -m pytest -q
```

The origin can run locally without `ORIGIN_AUTH_TOKEN`. For local Worker-to-origin or container testing, set the same high-entropy `ORIGIN_AUTH_TOKEN` in both environments. In production the Worker requires this secret and passes it only to the container; requests carrying a caller-supplied header cannot override it.

Configuration variables: `MAX_UPLOAD_BYTES`, `MAX_MULTIPART_OVERHEAD_BYTES`, `MAX_PIXELS`, `MAX_DIMENSION`, `MAX_OUTPUT_BYTES`, `MAX_CONCURRENT_REQUESTS`, `REQUEST_TIMEOUT_SECONDS`, `UPLOAD_TIMEOUT_SECONDS`, `ORIGIN_RATE_LIMIT`, and `ORIGIN_RATE_PERIOD_SECONDS`. Keep limits conservative and benchmark before increasing them.

## Container

Build from the repository root:

```bash
docker build -t naymark-api .
docker run --rm -p 8080:8080 naymark-api
```

The image uses Python 3.12, the `requirements.lock` constraints, one Uvicorn worker, bounded request concurrency, and a non-root runtime user. `services/api/Dockerfile` is an equivalent alternative when invoked with the repository root as the build context.

## Cloudflare deployment

The gateway is a Cloudflare Worker and the Python origin is a Cloudflare Container accessed only through a bound Durable Object. The container runs Linux/amd64, Python 3.12, and headless OpenCV; it is configured as a `standard-1` instance (0.5 vCPU, 4 GiB) with a maximum of eight instances and no outbound Internet access. Cloudflare documents Containers as available on a Workers Paid plan. Check current account limits, container support, cold starts, native wheel compatibility, latency, throughput, and cost in staging before production.

1. Use a Cloudflare account with Workers Paid, enable billing, and make sure Docker is available to Wrangler for the first image build/deploy.
2. From `workers/api-gateway`, install dependencies (`npm ci`) and deploy once with `npx wrangler deploy`.
3. Configure the `ORIGIN_AUTH_TOKEN` Worker secret with `npx wrangler secret put ORIGIN_AUTH_TOKEN`. For local `wrangler dev`, use an ignored `.dev.vars` file containing a separate development token.
4. Configure a custom domain/route on a proxied Cloudflare zone if you need zone WAF features. Enable Cloudflare managed WAF protections and DDoS protections as applicable to the account; add a rule that bypasses cache for `/v1/process` (and preferably all API paths). The Worker itself never uses Cache API and enforces `private, no-store` on all responses.
5. Before launch, exercise large/malformed uploads, rate limits, concurrency, cold starts, and origin failures; review Cloudflare Container logs/metrics and tune the image/pixel/time budgets.

The Worker enforces request-size limits even when `Content-Length` is absent, rate-limits processing calls, sends a fresh correlation ID, and overwrites origin-authentication headers. The per-location Workers Rate Limiting API is approximate; it is not a strict global quota. Cloudflare's default managed DDoS protections and any account-specific WAF features still require account/zone setup; they are not provisioned by this repository.

## GitHub Actions

`.github/workflows/build-and-test.yml` runs the focused Python API/admission tests, builds the Docker image, and runs Worker edge tests plus TypeScript checks. `.github/workflows/deploy.yml` deploys the Worker and Container from `main` or manually, using the protected `production` GitHub Environment.

Create the `production` environment, restrict deployments to the intended branch, and configure required reviewers. Add these environment secrets:

- `CLOUDFLARE_API_TOKEN` — least-privilege token scoped to the target account's Workers/Containers deployment.
- `CLOUDFLARE_ACCOUNT_ID` — target account ID.
- `ORIGIN_AUTH_TOKEN` — high-entropy secret, distinct from all other tokens.

The workflow does not place secrets in source control. Cloudflare's current Wrangler CI guidance requires an account ID and API token; it does not document GitHub OIDC federation for this deployment flow. Use a short-lived/scoped token if available, rotate it, and do not claim OIDC is active unless Cloudflare supports and the account configures it. An initial deploy may be needed before Wrangler can set the Worker secret; the workflow deploys once, writes the secret through Wrangler stdin, and never prints it.

No Azure resources, database, migrations, R2 bucket, or external origin are part of this design. Deployment was not run from this workspace; account-specific setup and staging validation remain prerequisites.
