"""Compatibility alias: this module lives in the shared library (common/aurix_common/crypto.py)."""
import sys

from aurix_common import crypto as _module

sys.modules[__name__] = _module
