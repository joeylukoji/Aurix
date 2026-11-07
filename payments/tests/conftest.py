import importlib.util
import os
from pathlib import Path

os.environ["AURIX_NO_AUTOAPP"] = "1"


def _load_sim():
    """The local PayPal simulator (paypal-sim/), loaded in-process for the PayPal flow tests."""
    spec = importlib.util.spec_from_file_location("paypal_sim", Path(__file__).resolve().parents[2] / "paypal-sim/app/main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SIM = _load_sim()
