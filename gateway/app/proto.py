"""Compatibility alias: this module lives in the shared library (common/aurix_common/proto.py)."""
import sys

from aurix_common import proto as _module

sys.modules[__name__] = _module
