FROM golang:1.24-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go build -o /aurixd ./cmd/aurixd

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends jq ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /aurixd /usr/local/bin/aurixd
COPY scripts/init-local.sh /usr/local/bin/init-local.sh
ENV AURIX_HOME=/data/aurix
VOLUME /data
# 26657: CometBFT RPC, 1317: REST API, 9090: gRPC
EXPOSE 26657 1317 9090
CMD ["sh", "-c", "init-local.sh && exec aurixd start --home $AURIX_HOME"]
