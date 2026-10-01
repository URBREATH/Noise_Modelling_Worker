# Stage 1: build the NoiseModelling CLI (without GUI) from the official source
FROM eclipse-temurin:11-jdk AS build
ARG NM_VERSION=5.0.1

RUN apt-get update \
 && apt-get install -y --no-install-recommends git unzip \
 && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch v${NM_VERSION} \
      https://github.com/Universite-Gustave-Eiffel/NoiseModelling.git /src

WORKDIR /src/wps_scripts
# Pin the core library: build.gradle asks for any version in [5.0, 6.0)
RUN sed -i "s/version: '\[5.0, 6.0)'/version: '${NM_VERSION}'/" build.gradle \
 && ./gradlew --no-daemon assembleDist -x test -x javadoc \
 && unzip -q build/distributions/NoiseModelling_without_gui-*.zip -d /opt \
 && mv /opt/NoiseModelling_without_gui-* /opt/noisemodelling

# Stage 2: runtime image, Java only
FROM eclipse-temurin:11-jre
COPY --from=build /opt/noisemodelling /opt/noisemodelling
COPY urbreath_pipeline.groovy /opt/noisemodelling/urbreath_pipeline.groovy

# Sub-scripts are referenced as noisemodelling/wps/..., relative to this folder
WORKDIR /opt/noisemodelling
ENTRYPOINT ["./bin/wps_scripts"]