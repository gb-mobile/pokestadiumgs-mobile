#!/usr/bin/env python3
"""Verify the bundled N-Rage build used for Mobile Crystal interop.

This exact build supports MBC30 SRAM banks 4-7 and eagerly initializes a
configured Transfer Pak when the N64 ROM opens.  The latter is required because
Stadium 2 can begin raw pak I/O before N-Rage's lazy controller-status path.
"""

import argparse
import hashlib
from pathlib import Path

EXPECTED_SHA256 = "ea80ac2da3a467d281fe17867b0e5cbd9d7d4dcde149a08b78d2918325cbfb8b"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plugin", type=Path)
    args = parser.parse_args()
    if not args.plugin.is_file():
        parser.error(f"plugin not found: {args.plugin}")
    actual = hashlib.sha256(args.plugin.read_bytes()).hexdigest()
    if actual != EXPECTED_SHA256:
        parser.error(
            f"wrong N-Rage plugin build: {actual}; expected {EXPECTED_SHA256}"
        )
    print(f"MBC30/startup-initialization N-Rage plugin verified: {args.plugin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
