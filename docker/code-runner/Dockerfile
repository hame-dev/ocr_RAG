# Sandbox for model-written Python (General chat). See server.py for the
# isolation layers; the rest are in docker-compose.yml.
FROM python:3.13-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MPL_CACHE_SEED=/opt/mplconfig

# Noto covers Arabic, so charts with Arabic labels render real glyphs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt

# Build matplotlib's font cache once; server.py copies it into each job.
RUN mkdir -p /opt/mplconfig \
    && MPLCONFIGDIR=/opt/mplconfig python -c "import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt; plt.figure()" \
    && chmod -R a+rX /opt/mplconfig

RUN useradd --create-home --uid 10001 runner
COPY server.py sandbox_exec.py /srv/
USER runner
WORKDIR /tmp

EXPOSE 8090
CMD ["python", "/srv/server.py"]
