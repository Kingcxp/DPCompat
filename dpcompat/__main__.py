"""Module entry point for ``python -m dpcompat``.

Keep this file deliberately small: command-line parsing belongs in :mod:`dpcompat.cli`,
which is easier to import and test without starting a subprocess.
"""

from .cli import main

if __name__ == "__main__":
    main()
