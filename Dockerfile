# ── Stage 1: Build React UI ──────────────────────────────────────────────────
FROM node:20-slim AS ui-builder
WORKDIR /app/f1-ui
COPY f1-ui/package*.json ./
RUN npm ci
COPY f1-ui/ ./
RUN npm run build

# ── Stage 2: Python app ───────────────────────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    python3-dev \
    libfreetype6-dev \
    libpng-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Copy built React UI from Stage 1
COPY --from=ui-builder /app/f1-ui/dist /app/f1-ui/dist

EXPOSE 8080
CMD ["python", "main.py"]
