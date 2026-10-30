"""``python -m srev.run --config FILE`` is shorthand for ``srev run --config FILE``."""

import sys

from srev.cli import main

if __name__ == "__main__":
    sys.exit(main(["run", *sys.argv[1:]]))
