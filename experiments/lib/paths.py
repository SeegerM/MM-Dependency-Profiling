"""Resolves machine-local paths from config/paths.env.

Scripts import this rather than hardcoding absolute paths, so the repo moves between machines by
editing one file. paths.local.env overrides paths.env when present and is gitignored.
"""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load():
    values = {}
    for name in ("paths.env", "paths.local.env"):
        path = REPO / "config" / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    values.update({k: v for k, v in os.environ.items() if k in values})
    return values


_VALUES = _load()

def _resolve(key):
    """Absolute paths pass through; relative ones are taken against the repo root, so submodule
    locations survive being run from any working directory."""
    value = Path(_VALUES[key])
    return value if value.is_absolute() else (REPO / value)


SPIND_HOME = _resolve("SPIND_HOME")
HYFD_HOME = _resolve("HYFD_HOME")
HYUCC_HOME = _resolve("HYUCC_HOME")
DATA_ROOT = _resolve("DATA_ROOT")
WORK_ROOT = _resolve("WORK_ROOT")
RESULT_ROOT = _resolve("RESULT_ROOT")

# Repo-relative, same on every machine.
RESULTS = REPO / "experiments" / "results"
TABLES = REPO / "paper" / "tables"

# The generated three-model dataset and its ground truth manifest.
DATASET = WORK_ROOT / "mm_tpch"
GROUND_TRUTH = DATASET / "ground_truth.json"


def canonical(variant, dataset="multimodel", layer=1):
    """Path to a discovery run's canonically ordered pIND dump."""
    return RESULT_ROOT / f"{variant}_{dataset}" / f"{layer}-ary_canonical.txt"
