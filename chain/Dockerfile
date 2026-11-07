FROM golang:1.24-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go build -o /aurixd ./cmd/aurixd

FROM debian:bookworm-slim
# init-local.sh needs bash + jq; curl is used by the healthcheck.
# Runs as root on purpose: Render persistent disks are mounted root-owned at /data.
RUN apt-get update && apt-get install -y --no-install-recommends bash jq curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data
COPY --from=build /aurixd /usr/local/bin/aurixd
COPY scripts/init-local.sh /usr/local/bin/init-local.sh
RUN chmod +x /usr/local/bin/init-local.sh
ENV AURIX_HOME=/data/aurix
VOLUME /data
# 26657: CometBFT RPC, 1317: REST API, 9090: gRPC
EXPOSE 26657 1317 9090
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
  CMD curl -fsS http://localhost:26657/status >/dev/null || exit 1
CMD ["bash", "-c", "init-local.sh && exec aurixd start --home \"$AURIX_HOME\""]
