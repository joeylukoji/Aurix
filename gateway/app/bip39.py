"""Compatibility alias: this module lives in the shared library (common/aurix_common/bip39.py)."""
import sys

from aurix_common import bip39 as _module

sys.modules[__name__] = _module
