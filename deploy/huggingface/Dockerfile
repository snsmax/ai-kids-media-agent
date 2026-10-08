FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --uid 1000 --create-home app
WORKDIR /home/app/platform
COPY pyproject.toml requirements.lock .env.example ./
COPY media ./media
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
RUN mkdir -p /data/videos && chown app:app /data/videos
USER app
EXPOSE 7860
CMD ["python", "-m", "media.runtime"]
