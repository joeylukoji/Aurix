#!/usr/bin/env bash
# Downloads the PaySim CSV and checks it is byte-identical to Kaggle's
# PS_20174392719_1491204439457_log.csv (dataset ealaxi/paysim1, CC BY-SA 4.0).
# Preferred source: Kaggle CLI (needs a Kaggle account + API token):
#   kaggle datasets download -d ealaxi/paysim1 && unzip paysim1.zip
# Fallback used for this project (Kaggle was not reachable from the build environment):
# a public Git LFS copy of the same file on GitHub, verified by SHA-256.
set -euo pipefail
OUT=${1:-paysim.csv}
SHA=16910f90577b0d981bf8ff289714510bb89bc71bff7d3f220f024e287e4eea6b
curl -fL -o "$OUT" "https://media.githubusercontent.com/media/ayaan2304/MoMTSim/HEAD/data/paysim.csv"
echo "$SHA  $OUT" | sha256sum -c -
