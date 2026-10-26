# syntax=docker/dockerfile:1

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tesseract-ocr is the OCR engine behind pytesseract. The app runs without it
# (OCR returns ""), but images then contribute no searchable text.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY api ./api
COPY vercel.json ./

# Run as a non-root user: a container that can write to its own source tree is
# one file-upload bug away from a code-execution path.
RUN useradd --create-home --uid 10001 aurora \
    && mkdir -p /app/storage \
    && chown -R aurora:aurora /app
USER aurora

ENV STORAGE_DIR=/app/storage \
    APP_ENV=production \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
