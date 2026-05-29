# Stage 1: Build frontend
FROM node:20-alpine AS frontend-builder

WORKDIR /app/dashboard

COPY dashboard/package*.json ./
RUN npm ci

COPY dashboard/ ./
RUN npm run build

# Stage 2: Serve frontend and run backend
FROM python:3.11-slim

# Install nginx for serving frontend
RUN apt-get update && apt-get install -y nginx && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY core/ ./core/
COPY gateway.py .
COPY base.py .
COPY data/ ./data/

# Copy built frontend
COPY --from=frontend-builder /app/dashboard/dist /app/dist

# Configure nginx for Vue SPA
RUN echo 'server { \
    listen 80; \
    server_name _; \
    index index.html; \
    location / { \
        root /app/dist; \
        try_files $uri $uri/ /index.html; \
    } \
}' > /etc/nginx/sites-available/default

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Start nginx and uvicorn
CMD service nginx start && \
    exec uvicorn gateway:app --host 0.0.0.0 --port 8000
