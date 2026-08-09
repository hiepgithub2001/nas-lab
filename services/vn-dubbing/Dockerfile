FROM python:3.11.13-slim-bookworm

ARG INSTALL_VOXCPM=false

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/state/model-cache/huggingface \
    XDG_CACHE_HOME=/state/model-cache

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml requirements-tts.txt requirements-voxcpm.txt ./

# CUDA wheels carry their userspace runtime and use the host driver through the
# NVIDIA container runtime. VieNeu's standard v2 backend is selected in code;
# the default v3 factory mode is never used.
RUN python -m pip install --no-cache-dir \
      torch==2.8.0 torchaudio==2.8.0 \
      --index-url https://download.pytorch.org/whl/cu128

RUN python -m pip install --no-cache-dir \
      PyYAML==6.0.2 vieneu==3.2.4 transformers==4.57.6 \
    && if [ "$INSTALL_VOXCPM" = "true" ]; then \
         python -m pip install --no-cache-dir -r requirements-voxcpm.txt; \
       fi

COPY src ./src
COPY profiles ./profiles
RUN python -m pip install --no-cache-dir --no-deps .

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app
USER app

ENTRYPOINT ["vn-dub"]
CMD ["supervisor"]
