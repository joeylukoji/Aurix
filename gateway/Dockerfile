# Build context: repository root (needs common/, gateway/ and web/).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY common /srv/common
WORKDIR /srv/gateway
COPY gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd -m -u 10001 aurix && mkdir -p /data && chown aurix:aurix /data
COPY gateway/app ./app
COPY web /srv/web
ENV WEB_DIR=/srv/web DATABASE_URL=sqlite:////data/aurix.db PORT=8080
USER aurix
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=6 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://localhost:%s/api/health'%os.environ.get('PORT','8080'),timeout=4)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
