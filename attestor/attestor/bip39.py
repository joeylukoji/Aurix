"""BIP39 mnemonic validation (English wordlist + checksum), stdlib only.

The wordlist (bip39-english.txt, 2048 words) is copied from web/vendor/bip39-english.js
(@scure/bip39 1.4.0). Same file in attestor/attestor/.
"""
from __future__ import annotations

import hashlib
import unicodedata
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def wordlist() -> tuple[str, ...]:
    words = tuple((Path(__file__).with_name("bip39-english.txt")).read_text().split())
    if len(words) != 2048:
        raise RuntimeError("corrupt BIP39 wordlist")
    return words


def validate(mnemonic: str) -> list[str]:
    """Returns the normalized word list; raises ValueError on bad length, unknown word or checksum."""
    words = unicodedata.normalize("NFKD", mnemonic).lower().split()
    if len(words) not in (12, 15, 18, 21, 24):
        raise ValueError("mnemonic must have 12, 15, 18, 21 or 24 words")
    index = {w: i for i, w in enumerate(wordlist())}
    bad = [w for w in words if w not in index]
    if bad:
        raise ValueError(f"mnemonic word #{words.index(bad[0]) + 1} is not in the BIP39 English wordlist")
    bits = "".join(format(index[w], "011b") for w in words)
    cs = len(words) // 3  # checksum bits = entropy bits / 32
    entropy = int(bits[:-cs], 2).to_bytes((len(bits) - cs) // 8, "big")
    expected = format(hashlib.sha256(entropy).digest()[0], "08b")[:cs]
    if bits[-cs:] != expected:
        raise ValueError("invalid mnemonic checksum (typo or wrong word order?)")
    return words
