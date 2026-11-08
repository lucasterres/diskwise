#!/usr/bin/env python3
"""Entry point: python scripts/diskwise.py <command> [...]  (stdlib only, Python 3.9+)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from diskkit.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
