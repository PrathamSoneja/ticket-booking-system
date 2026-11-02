FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python scripts/generate_proto.py

ENV PYTHONUNBUFFERED=1

