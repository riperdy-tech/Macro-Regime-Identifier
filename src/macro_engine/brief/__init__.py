"""The daily macro brief (WO_MB1, Layer B of SPEC_macro_combined_v0.1).

It brings the regime engine's INPUTS up to date from dated, quoted official releases and then lets the engine's own
arithmetic give a second reading, "as of today", beside "as measured". The model never names a regime.
Nothing reads the brief: no sector tilt, no book, no valuation input.
"""

PROMPT_VERSION = "mb1-v1"
