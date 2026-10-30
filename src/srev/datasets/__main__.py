"""``python -m srev.datasets ...`` is shorthand for ``srev datasets ...``."""

import sys

from srev.cli import main

sys.exit(main(["datasets", *sys.argv[1:]]))
