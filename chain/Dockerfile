FROM golang:1.24-alpine AS build
WORKDIR /src
COPY . .
RUN go build -o /aurixd ./cmd/aurixd
FROM alpine:3.20
COPY --from=build /aurixd /usr/local/bin/aurixd
EXPOSE 26657
CMD ["aurixd"]
