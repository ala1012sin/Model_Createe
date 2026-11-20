"""python -m mlworkbench.runner <workspace> <run_id> — 서버가 시작하는 worker 진입점."""

import sys

from mlworkbench.runner.worker import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
