FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY requirements.lock /app/requirements.lock
COPY services/api/app /app/services/api/app
RUN pip install --no-cache-dir -c requirements.lock .

USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--limit-concurrency", "16", "--timeout-keep-alive", "5", "--proxy-headers"]
