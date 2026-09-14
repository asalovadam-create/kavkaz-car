# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Системные зависимости для psycopg2 и Pillow (сборка колёс с нуля не нужна
# для большинства платформ, но libpq нужен psycopg2-binary в рантайме).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

RUN useradd --create-home appuser \
    && mkdir -p /app/static/uploads \
    && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    FLASK_DEBUG=0

EXPOSE 8000

CMD ["/app/start.sh"]