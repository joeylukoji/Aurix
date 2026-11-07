"""Compatibility alias: this module lives in the shared library (common/aurix_common/clients.py)."""
import sys

from aurix_common import clients as _module

sys.modules[__name__] = _module
