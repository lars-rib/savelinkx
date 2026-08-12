FROM python:3.12-slim

# ffmpeg is not optional: yt-dlp needs it to merge separate video and audio
# streams, which is most of the higher-quality formats.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 savelinkx \
    && mkdir -p /app/downloads \
    && chown -R savelinkx:savelinkx /app
USER savelinkx

EXPOSE 5000

# One worker with a long timeout: downloads are long-lived streaming responses,
# so scale with more containers rather than more threads per container.
CMD ["gunicorn", "app:app", "--workers", "1", "--timeout", "300", "--bind", "0.0.0.0:5000"]
