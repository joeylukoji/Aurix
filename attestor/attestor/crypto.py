"""secp256k1 keys + SIGN_MODE_DIRECT tx building (copied from gateway/app/crypto.py, trimmed).

Private key = sha256(seed string); address = bech32("aur", ripemd160(sha256(compressed pubkey))).
"""
from __future__ import annotations

import hashlib

from Crypto.Hash import RIPEMD160
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed, decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from . import proto

PREFIX = "aur"
DENOM = "uaur"
GAS_LIMIT = 300_000
FEE = 2_000  # uaur; min gas price 0.0025uaur -> 750uaur required
ANCHOR_TYPE_URL = "/aurix.smartmoney.v1.MsgAnchorProof"
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values):
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk


def _hrp_expand(hrp):
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _convertbits(data, frombits, tobits):
    acc = bits = 0
    ret, maxv = [], (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if bits:
        ret.append((acc << (tobits - bits)) & maxv)
    return ret


def bech32_encode(hrp: str, data: bytes) -> str:
    d = _convertbits(data, 8, 5)
    chk = _polymod(_hrp_expand(hrp) + d + [0] * 6) ^ 1
    return hrp + "1" + "".join(_CHARSET[x] for x in d + [(chk >> 5 * (5 - i)) & 31 for i in range(6)])


class Key:
    def __init__(self, seed: str):
        d = int.from_bytes(hashlib.sha256(seed.encode()).digest(), "big")
        self._sk = ec.derive_private_key(d, ec.SECP256K1())
        self.pubkey = self._sk.public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint)
        self.address = bech32_encode(PREFIX, RIPEMD160.new(hashlib.sha256(self.pubkey).digest()).digest())

    def sign(self, data: bytes) -> bytes:
        """64-byte r||s over sha256(data), low-S normalised as Cosmos requires."""
        digest = hashlib.sha256(data).digest()
        r, s = decode_dss_signature(self._sk.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256()))))
        if s > _N // 2:
            s = _N - s
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    def verify(self, data: bytes, sig: bytes) -> bool:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
        try:
            self._sk.public_key().verify(der, data, ec.ECDSA(hashes.SHA256()))
            return True
        except InvalidSignature:
            return False


def msg_anchor_proof(signer: str, hash_hex: str, external_ref: str, capture_ref: str) -> bytes:
    """Any-wrapped MsgAnchorProof {signer=1, hash=2, external_ref=3, capture_ref=4}."""
    return proto.any_(ANCHOR_TYPE_URL, proto.string(1, signer) + proto.string(2, hash_hex)
                      + proto.string(3, external_ref) + proto.string(4, capture_ref))


def sign_doc(body: bytes, auth_info: bytes, chain_id: str, account_number: int) -> bytes:
    return proto.bytes_(1, body) + proto.bytes_(2, auth_info) + proto.string(3, chain_id) + proto.uint(4, account_number)


def build_tx(key: Key, msgs: list[bytes], chain_id: str, account_number: int, sequence: int,
             memo: str = "") -> bytes:
    """Builds and signs a TxRaw with SIGN_MODE_DIRECT."""
    body = b"".join(proto.msg(1, m) for m in msgs) + proto.string(2, memo)
    pub_any = proto.any_("/cosmos.crypto.secp256k1.PubKey", proto.bytes_(1, key.pubkey))
    mode_info = proto.msg(1, proto.uint(1, 1))  # single { mode: SIGN_MODE_DIRECT }
    signer_info = proto.msg(1, pub_any) + proto.msg(2, mode_info) + proto.uint(3, sequence)
    fee = proto.msg(1, proto.coin(DENOM, FEE)) + proto.uint(2, GAS_LIMIT)
    auth_info = proto.msg(1, signer_info) + proto.msg(2, fee)
    sig = key.sign(sign_doc(body, auth_info, chain_id, account_number))
    return proto.bytes_(1, body) + proto.bytes_(2, auth_info) + proto.bytes_(3, sig)
