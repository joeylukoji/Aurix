"""Cosmos SDK transaction signing (secp256k1, SIGN_MODE_DIRECT) without heavy deps.

Keys: BIP39 mnemonic -> BIP32 m/44'/118'/0'/0/0 (cosmos standard, same address as
`aurixd keys add --recover`), a raw 32-byte private key, or (dev only) sha256(seed string),
the legacy derivation of the demo web wallet and `aurixd seed-address`. Addresses: bech32("aur", ripemd160(sha256(compressed pubkey))).
"""
from __future__ import annotations

import hashlib
import hmac
import unicodedata

from Crypto.Hash import RIPEMD160
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed, decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from . import proto

PREFIX = "aur"
DENOM = "uaur"
MICRO = 1_000_000
GAS_LIMIT = 300_000
FEE = 2_000  # uaur; min gas price is 0.0025uaur -> 750uaur required
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

# ---------------- bech32 ----------------
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


def _convertbits(data, frombits, tobits, pad=True):
    acc = bits = 0
    ret, maxv = [], (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad and bits:
        ret.append((acc << (tobits - bits)) & maxv)
    elif not pad and (bits >= frombits or ((acc << (tobits - bits)) & maxv)):
        raise ValueError("invalid padding")
    return ret


def bech32_encode(hrp: str, data: bytes) -> str:
    d = _convertbits(data, 8, 5)
    chk = _polymod(_hrp_expand(hrp) + d + [0] * 6) ^ 1
    return hrp + "1" + "".join(_CHARSET[x] for x in d + [(chk >> 5 * (5 - i)) & 31 for i in range(6)])


def bech32_decode(addr: str) -> tuple[str, bytes]:
    addr = addr.lower()
    pos = addr.rfind("1")
    hrp, data = addr[:pos], [_CHARSET.index(c) for c in addr[pos + 1:]]
    if pos < 1 or len(data) < 6 or _polymod(_hrp_expand(hrp) + data) != 1:
        raise ValueError("invalid bech32 checksum")
    return hrp, bytes(_convertbits(data[:-6], 5, 8, False))


def valid_address(addr: str) -> bool:
    try:
        hrp, data = bech32_decode(addr)
        return hrp == PREFIX and len(data) in (20, 32)
    except (ValueError, IndexError):
        return False


# ---------------- keys ----------------
COSMOS_PATH = "m/44'/118'/0'/0/0"


def _pubkey(k: int) -> bytes:
    return ec.derive_private_key(k, ec.SECP256K1()).public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint)


def mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
    """BIP39 seed (PBKDF2-HMAC-SHA512, 2048 rounds) after checking words + checksum (app/bip39.py)."""
    from .bip39 import validate
    words = validate(mnemonic)
    return hashlib.pbkdf2_hmac("sha512", " ".join(words).encode(),
                               ("mnemonic" + unicodedata.normalize("NFKD", passphrase)).encode(), 2048)


def derive_private_key(seed: bytes, path: str = COSMOS_PATH) -> bytes:
    """BIP32 private key derivation along `path` (e.g. m/44'/118'/0'/0/0)."""
    i = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    k, chain = int.from_bytes(i[:32], "big"), i[32:]
    parts = path.split("/")
    if parts[0] != "m":
        raise ValueError("path must start with m")
    for part in parts[1:]:
        hardened = part.endswith("'")
        idx = int(part.rstrip("'")) + (0x80000000 if hardened else 0)
        data = (b"\x00" + k.to_bytes(32, "big") if hardened else _pubkey(k)) + idx.to_bytes(4, "big")
        i = hmac.new(chain, data, hashlib.sha512).digest()
        il = int.from_bytes(i[:32], "big")
        if il >= _N:
            raise ValueError("invalid child key")
        k, chain = (il + k) % _N, i[32:]
        if k == 0:
            raise ValueError("invalid child key")
    return k.to_bytes(32, "big")


class Key:
    def __init__(self, seed: str | None = None, *, private_key: bytes | None = None):
        """Key(seed) = legacy dev derivation sha256(seed); Key(private_key=32 bytes) for real keys."""
        if private_key is None:
            private_key = hashlib.sha256(seed.encode()).digest()
        d = int.from_bytes(private_key, "big")
        if len(private_key) != 32 or not 0 < d < _N:
            raise ValueError("invalid secp256k1 private key")
        self._sk = ec.derive_private_key(d, ec.SECP256K1())
        self.pubkey = self._sk.public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint)
        self.address = bech32_encode(PREFIX, RIPEMD160.new(hashlib.sha256(self.pubkey).digest()).digest())

    @classmethod
    def from_mnemonic(cls, mnemonic: str, path: str = COSMOS_PATH) -> "Key":
        return cls(private_key=derive_private_key(mnemonic_to_seed(mnemonic), path))

    def sign(self, data: bytes) -> bytes:
        """64-byte r||s over sha256(data), low-S normalised as Cosmos requires."""
        digest = hashlib.sha256(data).digest()
        r, s = decode_dss_signature(self._sk.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256()))))
        if s > _N // 2:
            s = _N - s
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")


# ---------------- messages ----------------
def msg_send(frm: str, to: str, amount: int) -> bytes:
    return proto.any_("/cosmos.bank.v1beta1.MsgSend",
                      proto.string(1, frm) + proto.string(2, to) + proto.msg(3, proto.coin(DENOM, amount)))


def msg_anchor_proof(signer: str, hash_hex: str, external_ref: str, capture_ref: str = "",
                     amount: str = "", currency: str = "") -> bytes:
    return proto.any_(proto.ANCHOR_PROOF_TYPE_URL,
                      proto.msg_anchor_proof(signer, hash_hex, external_ref, capture_ref, amount, currency))


def build_tx(key: Key, msgs: list[bytes], chain_id: str, account_number: int, sequence: int,
             memo: str = "") -> bytes:
    """Builds and signs a TxRaw with SIGN_MODE_DIRECT."""
    body = b"".join(proto.msg(1, m) for m in msgs) + proto.string(2, memo)
    pub_any = proto.any_("/cosmos.crypto.secp256k1.PubKey", proto.bytes_(1, key.pubkey))
    mode_info = proto.msg(1, proto.uint(1, 1))  # single { mode: SIGN_MODE_DIRECT }
    signer_info = proto.msg(1, pub_any) + proto.msg(2, mode_info) + proto.uint(3, sequence)
    fee = proto.msg(1, proto.coin(DENOM, FEE)) + proto.uint(2, GAS_LIMIT)
    auth_info = proto.msg(1, signer_info) + proto.msg(2, fee)
    sign_doc = proto.bytes_(1, body) + proto.bytes_(2, auth_info) + proto.string(3, chain_id) + proto.uint(4, account_number)
    return proto.bytes_(1, body) + proto.bytes_(2, auth_info) + proto.bytes_(3, key.sign(sign_doc))


def msg_create_escrow(sender: str, recipient: str, amount: int, deadline_height: int, arbiter: str = "") -> bytes:
    return proto.any_("/aurix.smartmoney.v1.MsgCreateEscrow",
                      proto.string(1, sender) + proto.string(2, recipient) + proto.string(3, arbiter)
                      + proto.msg(4, proto.coin(DENOM, amount)) + proto.uint(5, deadline_height))


def msg_settle_escrow(kind: str, signer: str, escrow_id: int) -> bytes:
    """kind: 'Release' or 'Refund'."""
    return proto.any_(f"/aurix.smartmoney.v1.Msg{kind}Escrow", proto.string(1, signer) + proto.uint(2, escrow_id))
