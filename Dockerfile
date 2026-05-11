FROM python:3.11-slim

WORKDIR /app

# Install Node.js 20 + Python deps in one image
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install frontend dependencies and build
COPY frontend/package.json frontend/yarn.lock ./frontend/
RUN cd frontend && npm install -g yarn && yarn install --frozen-lockfile

COPY frontend/ ./frontend/
RUN cd frontend && yarn build

# Install Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir aiofiles

# Copy backend
COPY backend/ ./backend/

ENV PYTHONPATH=/app

WORKDIR /app/backend

EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
