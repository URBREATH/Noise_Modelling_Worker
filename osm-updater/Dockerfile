ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OSM_DATA_DIR=/data/osm

# pyosmium wheels link against the system expat (not in the slim image).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY osm_updater/ osm_updater/
COPY config/ config/

VOLUME ["/data/osm"]

HEALTHCHECK --interval=30m --timeout=30s --start-period=24h \
    CMD ["python", "-m", "osm_updater", "health"]

ENTRYPOINT ["python", "-m", "osm_updater"]
CMD ["daemon"]