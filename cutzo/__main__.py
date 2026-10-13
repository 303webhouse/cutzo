"""`python -m cutzo` opens Cutzo (starting its engine if needed). Used by the desktop shortcut."""

import sys

from .launcher import main

sys.exit(main())
