"""Entry point: python -m app"""

import sys

from app import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
