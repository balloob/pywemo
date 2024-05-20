FROM gcr.io/oss-fuzz-base/base-builder-python:v1@sha256:3cfc3865c20af6f810947a1073a39682c313970f2e38c342a90fc0f1c82d25ab
RUN apt-get update && \
    apt-get install -y make autoconf automake libtool pip
COPY . $SRC/pywemo
WORKDIR pywemo
COPY .clusterfuzzlite/build.sh $SRC/
