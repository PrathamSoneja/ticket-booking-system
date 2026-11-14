FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
# the NLTK data download is retried: a single TLS/network hiccup used to fail the whole build
RUN python -c "import nltk, sys, time; ok = any(nltk.download('stopwords', download_dir='/usr/local/share/nltk_data', quiet=True) or time.sleep(5) for _ in range(5)); sys.exit(0 if ok else 1)"
COPY . .
RUN python scripts/generate_proto.py

ENV NLTK_DATA=/usr/local/share/nltk_data \
    PYTHONUNBUFFERED=1
