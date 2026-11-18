"""Generate new Hanoi task candidates in a separate JSON file."""
import sys

# Dataset tooling does not need persistent bytecode caches.
sys.dont_write_bytecode = True

from dataset_tools.generate import main

if __name__ == "__main__":
    raise SystemExit(main())
