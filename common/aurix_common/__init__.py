"""Shared library of the AURIX Python services (gateway, payments, indexer).

crypto/proto: secp256k1 keys, bech32 addresses and SIGN_MODE_DIRECT Cosmos txs;
bip39/keys: BIP39 mnemonics and key loading (dev seeds refused in production);
clients: chain (REST + CometBFT RPC) and PayPal REST clients; guardians: signed-score quorum.
"""
