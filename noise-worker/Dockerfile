FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# osmium-tool: the osmium command for "extract". libexpat1: needed by the pyosmium wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends osmium-tool libexpat1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY noise_worker/ noise_worker/
ENTRYPOINT ["python", "-m", "noise_worker"]