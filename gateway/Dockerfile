FROM python:3.12-slim
WORKDIR /srv/gateway
COPY gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway/app ./app
COPY web /srv/web
ENV WEB_DIR=/srv/web DATABASE_URL=sqlite:////data/aurix.db
VOLUME /data
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
