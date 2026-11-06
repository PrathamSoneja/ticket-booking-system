FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
RUN python -c "import nltk; assert nltk.download('stopwords', download_dir='/usr/local/share/nltk_data', quiet=True)"
COPY . .
RUN python scripts/generate_proto.py

ENV NLTK_DATA=/usr/local/share/nltk_data \
    PYTHONUNBUFFERED=1
