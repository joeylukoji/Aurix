# Build context: attestor/
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/attestor
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m -u 10001 aurix
COPY attestor ./attestor
ENV ATTESTOR_PORT=8095
# Signing key, in priority order (provide one at runtime, never bake it into the image):
#   ATTESTOR_MNEMONIC  BIP39 mnemonic, path m/44'/118'/0'/0/0
#   ATTESTOR_KEY_FILE  path to a mounted file with the 32-byte private key hex (mode 0600)
#   ATTESTOR_SEED      dev only (warning logged); refused when AURIX_ENV=production
# Other: CHAIN_REST, CHAIN_ID, PAYPAL_BASE_URL, PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, POLL_INTERVAL, LOG_LEVEL
USER aurix
EXPOSE 8095
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=6 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://localhost:%s/health'%os.environ.get('ATTESTOR_PORT','8095'),timeout=4)"
CMD ["python", "-m", "attestor"]
