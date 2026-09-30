# Debian trixie ships tesseract 5.5 (5.3 on bookworm) at the SAME
# /usr/share/tesseract-ocr/5/tessdata path, so the tessdata volume mount and the
# Arabic language packs carry over from the 3.11-slim image unchanged.
FROM python:3.13-slim-trixie

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

# Upgrading pip first matters on 3.13: older pip resolves some of the pinned
# wheels to sdists and then tries to compile them.
# INSTALL_DEV=1 (the compose default) adds pytest and the fixture tools, so
# `make test` runs in this image. Build a production image with INSTALL_DEV=0.
ARG INSTALL_DEV=1
COPY backend/requirements.txt backend/requirements-dev.txt /app/
RUN pip install --upgrade pip setuptools wheel \
    && pip install -r /app/requirements$( [ "$INSTALL_DEV" = "1" ] && echo "-dev" ).txt

COPY backend/ /app/

EXPOSE 8000
