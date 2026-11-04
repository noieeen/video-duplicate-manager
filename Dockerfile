# Headless CLI only. The PyQt5 GUI (ui.py) is not containerized.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch: ~1GB image instead of ~6GB with CUDA wheels.
RUN pip install --no-cache-dir torch==2.6.0 torchvision==0.21.0 \
      --index-url https://download.pytorch.org/whl/cpu
# PyPI "openai-clip" can lack clip.load(); install CLIP from GitHub.
RUN pip install --no-cache-dir ftfy regex tqdm pillow setuptools \
 && pip install --no-cache-dir --no-build-isolation git+https://github.com/openai/CLIP.git

COPY main.py docker/run.py ./

ENV PYTHONUNBUFFERED=1
CMD ["python", "run.py"]
