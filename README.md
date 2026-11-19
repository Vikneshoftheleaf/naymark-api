# Naymark API

[![naymark on PyPI](https://img.shields.io/pypi/v/naymark?logo=pypi&label=naymark)](https://pypi.org/project/naymark/)
![FastAPI](https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi&logoColor=white)
![Vercel](https://img.shields.io/badge/Deploy-Vercel-black?logo=vercel&logoColor=white)

A small FastAPI service that accepts an image and returns a processed PNG using [`naymark`](https://pypi.org/project/naymark/).

`naymark` supports specific visible watermark patterns in Gemini-generated images. Results are approximate; it is not a general-purpose watermark remover. Only process images you own or are authorized to modify.

The public Vercel site includes an interactive playground at `/` and API documentation at `/docs/`. The playground posts uploads to the same `/v1/process` endpoint described below.

## API

`POST /v1/process` accepts one multipart file field named `file`. JPEG, PNG, and WebP are supported. The response is a PNG image.

```bash
curl -X POST "https://YOUR-VERCEL-DOMAIN/v1/process" \
  -F "file=@image.png;type=image/png" \
  --output processed.png
```

`GET /healthz` returns `{"status":"ok"}`. The API is public and does not require authentication. Responses are not cached. Uploads are limited to 4 MiB and images to 12 megapixels.

## Run locally

Requires Python 3.12.

```bash
python -m venv .venv
```

Activate the environment, then install dependencies and start the API:

```bash
pip install -r requirements-dev.txt
uvicorn app:app --reload
```

Run tests with:

```bash
pytest
```

## Deploy to Vercel

This project uses Vercel's Python runtime; Docker is not required.

**From the Vercel dashboard:** Import this Git repository as a new project, keep the project root at the repository root, and deploy. Vercel installs dependencies from `requirements.txt`, serves the frontend from `public/`, and detects the FastAPI app in `app.py`.

**Using the CLI:**

```bash
npx vercel
npx vercel --prod
```

The `.python-version` file selects Python 3.12. Vercel's Python Functions have platform limits; the API keeps uploads small and returns errors for images or processed output that exceed its limits.
