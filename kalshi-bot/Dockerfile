# Imagen para desplegar el bot con su panel web (Railway, Render, un VPS...).
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    KALSHI_BOT_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kalshi_bot ./kalshi_bot
COPY config.example.toml ./config.toml
RUN mkdir -p /data

# El panel escucha en $PORT (lo pone la plataforma) o en el 8000.
EXPOSE 8000
CMD ["python", "-m", "kalshi_bot", "web"]
