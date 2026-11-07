#!/usr/bin/env bash
# Generates Go code from proto/ with protoc + protoc-gen-gocosmos.
# Requires: protoc (or `python -m grpc_tools.protoc`), protoc-gen-gocosmos@v1.7.0 and
# protoc-gen-grpc-gateway@v1.16.0 in PATH.
set -euo pipefail
cd "$(dirname "$0")/.."
M=$(go env GOMODCACHE)
PROTOC=${PROTOC:-protoc}
INC=(-I proto
  -I "$M/github.com/cosmos/cosmos-sdk@v0.50.10/proto"
  -I "$M/github.com/cosmos/gogoproto@v1.7.0"
  -I "$M/github.com/cosmos/cosmos-proto@v1.0.0-beta.5/proto"
  -I "$M/github.com/grpc-ecosystem/grpc-gateway@v1.16.0/third_party/googleapis")
OUT=$(mktemp -d)
$PROTOC "${INC[@]}" \
  --gocosmos_out=plugins=grpc,Mgoogle/protobuf/any.proto=github.com/cosmos/gogoproto/types/any:"$OUT" \
  --grpc-gateway_out=logtostderr=true,allow_colon_final_segments=true:"$OUT" \
  proto/aurix/smartmoney/v1/*.proto
cp "$OUT"/github.com/joeylukoji/aurix/chain/x/smartmoney/types/*.go x/smartmoney/types/
rm -rf "$OUT"
