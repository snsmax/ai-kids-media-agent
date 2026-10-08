FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY requirements.lock ./
COPY media ./media
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home app
RUN mkdir -p /data/videos && chown app:app /data/videos
USER app
EXPOSE 8000
CMD ["uvicorn", "media.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
