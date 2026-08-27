FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PULSERELAY_DB_PATH=/data/pulserelay.db

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 pulserelay \
    && mkdir -p /data \
    && chown -R pulserelay:pulserelay /app /data

# The container exposes the PulseRelay API/Webhooks and MCP control plane
# through one ASGI application on one public port.
COPY api.py ./
COPY server.py ./
COPY mcp_control.py ./
COPY assets ./assets
COPY connectors ./connectors
COPY core ./core
COPY workers ./workers

USER pulserelay
EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
