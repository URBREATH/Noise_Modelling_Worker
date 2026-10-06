FROM python:3.12-slim

# Java runtime + NoiseModelling CLI (built in the urbreath-nm image, Phase 3)
COPY --from=eclipse-temurin:11-jre /opt/java/openjdk /opt/java/openjdk
COPY --from=urbreath-nm /opt/noisemodelling /opt/noisemodelling
ENV JAVA_HOME=/opt/java/openjdk \
    PATH="/opt/java/openjdk/bin:${PATH}" \
    NM_HOME=/opt/noisemodelling

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