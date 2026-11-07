# Build context: attestor/
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/attestor
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m -u 10001 aurix
COPY attestor ./attestor
ENV ATTESTOR_PORT=8095
USER aurix
EXPOSE 8095
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=6 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://localhost:%s/health'%os.environ.get('ATTESTOR_PORT','8095'),timeout=4)"
CMD ["python", "-m", "attestor"]
