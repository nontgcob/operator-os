FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg unzip \
    && rm -rf /var/lib/apt/lists/*

ENV DENO_INSTALL=/usr/local
ENV YTDLP_JS_RUNTIME=deno:/usr/local/bin/deno

RUN curl -fsSL https://deno.land/install.sh -o /tmp/deno-install.sh \
    && sh /tmp/deno-install.sh \
    && rm /tmp/deno-install.sh \
    && /usr/local/bin/deno --version

WORKDIR /app
COPY services/video-service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN yt-dlp --js-runtimes deno:/usr/local/bin/deno --version
COPY services/video-service/app /app/app

EXPOSE 8002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
