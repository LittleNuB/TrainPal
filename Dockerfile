# syntax=docker/dockerfile:1.7

FROM node:24.14.1-bookworm-slim AS web-builder

WORKDIR /workspace
RUN corepack enable && corepack prepare pnpm@11.9.0 --activate

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/package.json
RUN pnpm install --frozen-lockfile --filter @hachimi/web...

COPY apps/web apps/web
COPY contracts/gymti-questionnaire.v1.json contracts/gymti-questionnaire.v1.json
ARG VITE_UPLOAD_MAX_BYTES=19000000
ENV VITE_UPLOAD_MAX_BYTES=${VITE_UPLOAD_MAX_BYTES}
RUN pnpm --filter @hachimi/web build

FROM ghcr.io/astral-sh/uv:0.11.7 AS uv-runtime

FROM debian:bookworm-slim AS ffmpeg-builder

ARG FFMPEG_VERSION=8.1.2
ARG FFMPEG_SIGNING_FINGERPRINT=FCF986EA15E6E293A5644F10B4322F04D67658D8
ARG FFMPEG_SOURCE_URL=https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update \
    && apt-get \
        -o Acquire::Retries=5 \
        -o Acquire::http::Timeout=30 \
        -o Binary::apt::APT::Keep-Downloaded-Packages=true \
        install --yes --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        gnupg \
        nasm \
        pkg-config \
        xz-utils

WORKDIR /src
RUN set -eu; \
    export GNUPGHOME="$(mktemp -d)"; \
    curl --fail --show-error --silent --location \
        "${FFMPEG_SOURCE_URL}" --output ffmpeg.tar.xz; \
    curl --fail --show-error --silent --location \
        "${FFMPEG_SOURCE_URL}.asc" --output ffmpeg.tar.xz.asc; \
    curl --fail --show-error --silent --location \
        https://ffmpeg.org/ffmpeg-devel.asc --output ffmpeg-devel.asc; \
    gpg --batch --import ffmpeg-devel.asc; \
    actual_fingerprint="$(gpg --batch --with-colons --fingerprint \
        "${FFMPEG_SIGNING_FINGERPRINT}" \
        | awk -F: '$1 == "fpr" { print $10; exit }')"; \
    test "${actual_fingerprint}" = "${FFMPEG_SIGNING_FINGERPRINT}"; \
    verification_status="$(gpg --batch --status-fd 1 \
        --verify ffmpeg.tar.xz.asc ffmpeg.tar.xz 2>/dev/null)"; \
    printf '%s\n' "${verification_status}" \
        | awk -v expected="${FFMPEG_SIGNING_FINGERPRINT}" \
            '$2 == "VALIDSIG" && ($3 == expected || $NF == expected) { valid = 1 } END { exit !valid }'; \
    rm -rf "${GNUPGHOME}" ffmpeg-devel.asc ffmpeg.tar.xz.asc; \
    tar --extract --file ffmpeg.tar.xz; \
    cd "ffmpeg-${FFMPEG_VERSION}"; \
    ./configure \
        --prefix=/opt/trainpal/ffmpeg \
        --enable-shared \
        --disable-static \
        --enable-pic \
        --disable-debug \
        --disable-doc \
        --disable-ffplay \
        --disable-ffprobe \
        --disable-network \
        --disable-autodetect \
        --disable-everything \
        --disable-programs \
        --enable-ffmpeg \
        --enable-avcodec \
        --enable-avdevice \
        --enable-avfilter \
        --enable-avformat \
        --enable-swresample \
        --enable-swscale \
        --enable-protocol=file,pipe \
        --enable-demuxer=mov,matroska \
        --enable-muxer=mp4,wav,image2,image2pipe \
        --enable-decoder=h264,hevc,mpeg4,vp8,vp9,av1,aac,mp3,opus,vorbis,pcm_s16le,wrapped_avframe \
        --enable-encoder=mpeg4,pcm_s16le,mjpeg,rawvideo \
        --enable-filter=scale,fps,tile,format,aformat,aresample,color \
        --enable-indev=lavfi \
        --enable-parser=h264,aac,mpeg4video; \
    make -j"$(nproc)"; \
    make install; \
    cp COPYING.LGPLv2.1 /opt/trainpal/ffmpeg/LICENSE.LGPLv2.1; \
    export LD_LIBRARY_PATH=/opt/trainpal/ffmpeg/lib; \
    configuration_line="$(/opt/trainpal/ffmpeg/bin/ffmpeg -version \
        | sed -n '/^configuration:/p')"; \
    test -n "${configuration_line}"; \
    case " ${configuration_line} " in \
        *" --enable-gpl "*|*" --enable-nonfree "*) exit 1 ;; \
    esac; \
    binary_sha256="$(sha256sum /opt/trainpal/ffmpeg/bin/ffmpeg | awk '{print $1}')"; \
    configuration_sha256="$(printf '%s' "${configuration_line}" | sha256sum \
        | awk '{print $1}')"; \
    printf '%s\n' \
        "{\"schema_version\":1,\"version\":\"${FFMPEG_VERSION}\",\"source_url\":\"${FFMPEG_SOURCE_URL}\",\"signing_key_fingerprint\":\"${FFMPEG_SIGNING_FINGERPRINT}\",\"binary_sha256\":\"${binary_sha256}\",\"configuration_line\":\"${configuration_line}\",\"configuration_sha256\":\"${configuration_sha256}\"}" \
        > /opt/trainpal/ffmpeg/receipt.json; \
    rm -f /src/ffmpeg.tar.xz

FROM python:3.12-slim-bookworm AS api-builder

COPY --from=uv-runtime /uv /uvx /bin/

WORKDIR /workspace
COPY services/analysis-api/pyproject.toml services/analysis-api/uv.lock services/analysis-api/
COPY services/analysis-api/src services/analysis-api/src

RUN uv sync --project services/analysis-api --frozen --no-dev \
    && find /workspace/services/analysis-api/.venv/lib -type f -path '*/imageio_ffmpeg/binaries/ffmpeg-*' -delete \
    && test -z "$(find /workspace/services/analysis-api/.venv/lib -type f -path '*/imageio_ffmpeg/binaries/ffmpeg-*' -print -quit)" \
    && rm -rf /root/.cache/uv

FROM python:3.12-slim-bookworm AS app-runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/workspace/services/analysis-api/src \
    WEB_STATIC_ROOT=/workspace/apps/web/dist \
    IMAGEIO_FFMPEG_EXE=/opt/trainpal/ffmpeg/bin/ffmpeg \
    FFMPEG_BUILD_RECEIPT_PATH=/opt/trainpal/ffmpeg/receipt.json \
    SOURCE_MANIFEST_PATH=/workspace/competition/media-manifest.json \
    SOURCE_MEDIA_ROOT=/workspace/tmp/controlled-media \
    LD_LIBRARY_PATH=/opt/trainpal/ffmpeg/lib

WORKDIR /workspace
COPY --from=api-builder /workspace/services/analysis-api/.venv services/analysis-api/.venv
COPY --from=ffmpeg-builder /opt/trainpal/ffmpeg /opt/trainpal/ffmpeg
COPY services/analysis-api/src services/analysis-api/src
COPY contracts/gymti-questionnaire.v1.json contracts/gymti-questionnaire.v1.json
COPY competition/media-manifest.json competition/media-manifest.json
COPY skills skills
COPY LICENSE THIRD_PARTY_NOTICES.md ./
COPY licenses licenses
COPY --from=web-builder /workspace/apps/web/dist apps/web/dist

RUN test -z "$(find /workspace/services/analysis-api/.venv/lib -type f -path '*/imageio_ffmpeg/binaries/ffmpeg-*' -print -quit)" \
    && test -x /opt/trainpal/ffmpeg/bin/ffmpeg \
    && test -f /opt/trainpal/ffmpeg/receipt.json \
    && groupadd --system --gid 10001 hachimi \
    && useradd --system --uid 10001 --gid hachimi --home-dir /nonexistent --shell /usr/sbin/nologin hachimi \
    && mkdir -p /workspace/tmp/analysis-runs \
    && chown -R hachimi:hachimi /workspace/tmp

USER 10001:10001
EXPOSE 8000

CMD ["services/analysis-api/.venv/bin/python", "-m", "hakimi_analysis.startup", "--", "services/analysis-api/.venv/bin/uvicorn", "hakimi_analysis.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
