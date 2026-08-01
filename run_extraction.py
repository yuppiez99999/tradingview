#!/usr/bin/env python3
"""Run the phase extraction script."""

import sys

sys.path.insert(0, '.')

from extract_phases import main

if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
