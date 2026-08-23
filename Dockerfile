FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PULSERELAY_DB_PATH=/data/pulserelay.db

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 pulserelay \
    && mkdir -p /data \
    && chown -R pulserelay:pulserelay /app /data

# The container exposes the webhook-native API and connector runtime.
COPY api.py ./
COPY assets ./assets
COPY connectors ./connectors
COPY core ./core
COPY workers ./workers

USER pulserelay
EXPOSE 8000

CMD ["uvicorn", "api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
