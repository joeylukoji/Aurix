# Build context: repository root (needs common/ and indexer/).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY common /srv/common
WORKDIR /srv/indexer
COPY indexer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd -m -u 10001 aurix && mkdir -p /data && chown aurix:aurix /data
COPY indexer/aurix_indexer ./aurix_indexer
ENV PORT=8086
USER aurix
EXPOSE 8086
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=6 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://localhost:%s/health'%os.environ.get('PORT','8086'),timeout=4)"
CMD ["sh", "-c", "exec uvicorn aurix_indexer.main:app --host 0.0.0.0 --port ${PORT:-8086}"]
