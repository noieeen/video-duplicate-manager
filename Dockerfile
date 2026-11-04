# Headless CLI for the duplicate engine (python -m engine). No torch, no GUI.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Static ffmpeg/ffprobe (~80MB) instead of apt's ffmpeg (~450MB of shared libs).
COPY --from=mwader/static-ffmpeg:7.1.1 /ffmpeg /ffprobe /usr/local/bin/

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY engine/ ./engine/

ENTRYPOINT ["python", "-m", "engine", "--db", "/data/library.db", "--human"]
CMD ["--help"]
