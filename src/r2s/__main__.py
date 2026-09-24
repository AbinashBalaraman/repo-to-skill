"""Let `python -m r2s` work as well as the `r2s` console script."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
