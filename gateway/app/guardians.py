"""Compatibility alias: this module lives in the shared library (common/aurix_common/guardians.py)."""
import sys

from aurix_common import guardians as _module

sys.modules[__name__] = _module
