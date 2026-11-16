# Imagen de la API (también ejecuta el arranque: carga de datos y primer modelo).
FROM python:3.11.10-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/api.txt

COPY config/ config/
COPY src/ src/
ENV PYTHONPATH=/app/src \
    NOVAMERCADO_ROOT=/app \
    NOVAMERCADO_CACHE_DIR=/app/cache \
    PYTHONUNBUFFERED=1 \
    GIT_PYTHON_REFRESH=quiet

RUN useradd --create-home --uid 1000 app && mkdir -p /app/cache && chown app /app/cache
USER app
EXPOSE 8000
CMD ["uvicorn", "novamercado.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
