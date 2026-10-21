FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libreoffice-writer \
       fonts-liberation \
       fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates ./templates

RUN useradd --system --uid 10001 --create-home appuser \
    && chown -R appuser:appuser /app

USER 10001

EXPOSE 5011

CMD ["gunicorn", "--bind", "0.0.0.0:5011", "--workers", "1", "--timeout", "150", "app:app"]