# Worker image: everything baked in so the machine starts working the second it boots.
# Build & push:  docker build -f worker/Dockerfile -t ghcr.io/<you>/docburst-worker:latest . && docker push ...
# Image pulls happen while the instance is "loading", before GPU billing starts.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 KOKORO_DIR=/opt/models/kokoro HOUSE_DIR=/opt/app/house \
    NVIDIA_DRIVER_CAPABILITIES=compute,video,utility
RUN apt-get update && apt-get install -y --no-install-recommends \
        melt ffmpeg frei0r-plugins python3 python3-pip curl ca-certificates \
        fonts-dejavu fonts-liberation espeak-ng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-worker.txt /tmp/
# kokoro-onnx pulls the CPU onnxruntime; swap it for the CUDA build (same module name).
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements-worker.txt && \
    pip3 uninstall -y --break-system-packages onnxruntime && \
    pip3 install --no-cache-dir --break-system-packages onnxruntime-gpu

RUN mkdir -p /opt/models/kokoro && cd /opt/models/kokoro && \
    for f in kokoro-v1.0.onnx voices-v1.0.bin; do \
      curl -fsSL -o $f https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f; done

COPY burst /opt/app/burst
COPY worker /opt/app/worker
COPY house /opt/app/house
# collage scene renderer (scene mode: `run.sh --scenes`, rented for CPU cores, GPU unused).
# The ISNet cut-out model is baked in so no scene waits on a download.
COPY collage /opt/app/collage
RUN cd /opt/app && python3 -c "from collage.assets import model_path; model_path('isnet-general-use.onnx'); model_path('u2netp.onnx')"
RUN python3 /opt/app/house/make_house.py /opt/app/house && chmod +x /opt/app/worker/run.sh

WORKDIR /opt/app
CMD ["/opt/app/worker/run.sh"]
