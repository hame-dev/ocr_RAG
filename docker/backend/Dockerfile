FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# tesseract-ocr-ara + the Arabic script pack are what make Arabic OCR work at
# all. libgl1/libglib2.0-0 are for opencv. The weasyprint stack (pango/cairo/
# harfbuzz) is what renders Arabic shaping + bidi correctly in test fixtures —
# ReportLab cannot do this without manual reshaping.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        tesseract-ocr \
        tesseract-ocr-ara \
        tesseract-ocr-eng \
        tesseract-ocr-script-arab \
        libgl1 \
        libglib2.0-0 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libharfbuzz0b \
        libfribidi0 \
        fonts-noto-core \
        fonts-noto-extra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY backend/ /app/

EXPOSE 8000
