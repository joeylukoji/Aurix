import pytest

from attestor import bip39
from attestor.keys import key_from_mnemonic as _kfm
from attestor.keys import KeyConfigError, load_key

ABOUT = "abandon " * 11 + "about"


def test_valid_vectors():
    assert bip39.validate(ABOUT)[-1] == "about"
    bip39.validate("legal winner thank year wave sausage worth useful legal winner thank yellow")
    bip39.validate(" ".join(["zoo"] * 23 + ["vote"]))


@pytest.mark.parametrize("bad, msg", [
    ("abandon " * 12, "checksum"),                     # all-zero entropy needs 'about'
    ("abandon " * 11 + "abandonx", "wordlist"),         # unknown word
    ("abandon " * 11, "12, 15"),                       # wrong length
    ("about " + "abandon " * 11, "checksum"),          # wrong order
])
def test_invalid_rejected(bad, msg):
    with pytest.raises(ValueError, match=msg):
        bip39.validate(bad)
    with pytest.raises(KeyConfigError):
        _kfm(bad)


def test_load_key_rejects_bad_checksum():
    with pytest.raises(KeyConfigError, match="checksum"):
        load_key(mnemonic="abandon " * 12)
    assert load_key(mnemonic=ABOUT).address.startswith("aur1")
