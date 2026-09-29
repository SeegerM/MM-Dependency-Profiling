"""Prints the file path of each observation set in a meta-level export, one per line."""
import json
import sys
from pathlib import Path

directory = Path(sys.argv[1])
for entry in json.loads((directory / "catalog.json").read_text(encoding="utf-8")):
    print(directory / entry["file"])
