# Naymark API Integration Plan

## Backend
- Project: `services/api`
- Run: `uvicorn app.main:app --app-dir services/api --host 0.0.0.0 --port 8080`
- Build: `docker build -t naymark-api .`
- Liveness: `GET /healthz`
- Readiness: `GET /readyz`
- Routes:
  - `POST /v1/process` — one multipart `file`; JPEG/PNG/WebP input; synchronous PNG result
  - `GET /healthz`
  - `GET /readyz`
- Security/limits: in-memory multipart parsing; 10 MiB file; 12 MP; 8,000 px/side; output capped at 40 MiB; bounded concurrency; origin token and per-process IP limiter.

## Frontend
- None. Skip frontend/live-data wiring and preview.

## Database
- None. NO seed data and NO migrations.

## Shared types
- None.

## Cloudflare
- Worker and Cloudflare Container Durable Object: `workers/api-gateway`
- Container build context: repository root (`workers/api-gateway/wrangler.toml`)
- Worker development/deploy: `npm ci`; `npx wrangler dev` / `npx wrangler deploy`
- Required Worker secret: `ORIGIN_AUTH_TOKEN`; Worker passes it to container at startup.
- Worker rate binding: `IMAGE_RATE_LIMIT`, 6 requests per minute per client key/location (approximate Cloudflare limit).
- Python container port: `8080`; health probe: `/healthz`; `standard-1`, max 8 instances; no outbound Internet.
- Worker/container path is provisioned in config, not deployed or staging-validated. Requires a Workers Paid account, working Docker, Cloudflare account ID, and protected GitHub environment.
- Direct container ingress is not exposed; the container is reached only via its Worker Durable Object binding.

## GitHub
- CI: `.github/workflows/build-and-test.yml` (pytest, Docker build, Worker typecheck)
- Deploy: `.github/workflows/deploy.yml` (protected `production` environment)
- Required environment secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `ORIGIN_AUTH_TOKEN`.
- Cloudflare's documented Wrangler CI path uses an account ID/API token; no OIDC federation is configured.

## Integration tasks
1. Run the tests and smoke-test all three routes against the container.
2. Verify supported behavior on authorized fixtures and unsupported inputs without writing image fixtures/results to disk.
3. Validate Docker/native OpenCV on Linux/amd64, Cloudflare Container availability and limits, cold starts, throughput, and rate limiting in staging.
4. Configure custom domain, managed WAF/DDoS protections, cache bypass for API paths, and GitHub environment approvals.
