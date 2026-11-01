import sys

from .cli import main, safe_stdout

safe_stdout()
sys.exit(main())
