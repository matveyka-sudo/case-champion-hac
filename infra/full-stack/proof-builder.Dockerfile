# Build only the source-bound native verifier, preserving the routing runtime.
FROM ghcr.io/valhalla/valhalla@sha256:4ac3d13d1019f9a130e9e6b6924c16ea55da0fa51e3653039152e33ad4095661
USER root
RUN apt-get update && apt-get install -y --no-install-recommends g++ pkg-config libprotobuf-dev zlib1g-dev libsqlite3-dev libspatialite-dev libluajit-5.1-dev libgeos-dev libcurl4-openssl-dev libgdal-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /source
