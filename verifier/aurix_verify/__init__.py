"""aurix-verify: independent verifier for AURIX evidence-escrow proof packages (MVP 0.2).

Pure library: no network, no database, no AURIX server code. The payments service imports the same
canonicalisation, signature and decision-rule code, so the verifier replays exactly the rule that was used.
"""
__version__ = "0.2.0"
