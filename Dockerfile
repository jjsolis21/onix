# Ónix FM — imagen de vista previa (API + frontend).
# El motor de audio corre en modo "mock" (sin sonido): bass.dll es solo Windows
# y el contenedor no tiene tarjeta de audio.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RADIO_DB_PATH=/data/radio_core.db \
    RADIO_MEDIA_ROOT=/media/onix

WORKDIR /app

COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY scripts ./scripts
COPY frontend ./frontend

EXPOSE 8000

# La BD se crea y migra sola en /data al arrancar (ya no viaja en el repo).
# api_server monta /stream al importarse, así que library/ debe existir antes.
CMD ["sh", "-c", "mkdir -p /data \"$RADIO_MEDIA_ROOT/library\" && exec uvicorn backend.api.api_server:app --host 0.0.0.0 --port 8000"]
