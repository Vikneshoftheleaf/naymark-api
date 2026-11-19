# Naymark

[![naymark on PyPI](https://img.shields.io/pypi/v/naymark?logo=pypi&label=naymark)](https://pypi.org/project/naymark/)
[![API documentation](https://img.shields.io/badge/API-documentation-2867B2)](https://naymark.vercel.app/docs/)
[![FastAPI](https://img.shields.io/badge/FastAPI-service-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Deployed on Vercel](https://img.shields.io/badge/deployed%20on-Vercel-black?logo=vercel&logoColor=white)](https://naymark.vercel.app/)

Remove Naymark's supported visible watermark from Gemini-generated images. Results are approximate; this is not a general-purpose watermark remover. Naymark is independent and is not affiliated with Google or Gemini.

## Try it

Use the [online image editor](https://naymark.vercel.app/) to upload an image, compare the result, and download a PNG.

## API

`POST https://naymark.vercel.app/v1/process`

Send one image as multipart form data in the `file` field. The response is a PNG.

```bash
curl -X POST "https://naymark.vercel.app/v1/process" \
  -F "file=@image.png;type=image/png" \
  --output processed.png
```

The API accepts image formats supported by Pillow, up to 4 MiB, 12 megapixels, and 8,000 pixels per dimension. It is public and does not require an API key. `GET /healthz` provides a health check.

## Python package

The [`naymark` package](https://pypi.org/project/naymark/) runs watermark matching directly in your Python application. Install it with:

```bash
pip install naymark
```

```python
import cv2
from naymark import remove_watermark

image = cv2.imread("image.png")
result = remove_watermark(image)
processed = getattr(result, "image", result)
cv2.imwrite("processed.png", processed)
```

The package processes images locally. Both the package and API target Naymark's supported visible watermark pattern; reconstruction may be imperfect. Only process images you own or are authorized to modify.
