FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
# libgomp1 is required by LightGBM at runtime. gcc/libc6-dev are only needed to build
# ed25519-blake2b (a bip_utils dependency without a wheel) and are removed in the same layer.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 gcc libc6-dev \
 && pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y gcc libc6-dev && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 10001 aurix
COPY app ./app
COPY models ./models
USER aurix
ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://localhost:%s/health'%os.environ.get('PORT','8000'),timeout=4)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
