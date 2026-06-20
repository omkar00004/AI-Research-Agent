# ---------- Stage 1: Build React frontend ----------
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: Python runtime ----------
FROM python:3.11-slim
WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy Python source
COPY server.py ./
COPY agents/ ./agents/
COPY utils/ ./utils/
COPY .env.example ./.env.example

# Copy built frontend from Stage 1
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Hugging Face Spaces uses port 7860
EXPOSE 7860

CMD ["python", "server.py"]
