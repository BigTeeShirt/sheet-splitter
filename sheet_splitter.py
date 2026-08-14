"""Entry point for the packaged Windows app.

Double-clicked, it opens the window. Sheets dragged onto the exe arrive as argv
and start splitting straight away.
"""

import multiprocessing
import sys

from sheetsplit.gui import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # harmless, and stops a frozen app re-launching itself
    sys.exit(main())
