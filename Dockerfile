FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

ENV PYTHONPATH=/app

WORKDIR /app/backend

EXPOSE 8000

CMD ["python", "server.py"]
