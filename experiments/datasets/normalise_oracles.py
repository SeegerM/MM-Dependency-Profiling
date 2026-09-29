"""Gives every instance's oracle the same two fields the readers expect.

Some oracles keep their list under another key (TPC-H uses `expected_inds`) or lack a `name`.
The original keys are left in place and `dependencies` and `name` are added beside them, so a file
that already has both is unchanged.

usage: normalise_oracles.py [--work=$WORK_ROOT] [--dry-run]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402

# Where each nonstandard shape keeps its dependency list.
ALIASES = ("dependencies", "expected_inds", "foreign_keys", "keys")


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


def normalise(path, instance, dry_run):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return "not an object, left alone"
    entries = None
    source_key = None
    for key in ALIASES:
        value = data.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict) \
                and "dependent" in value[0]:
            entries, source_key = value, key
            break
    if entries is None:
        return "no dependency list recognised, left alone"
    changed = []
    if "dependencies" not in data:
        data["dependencies"] = entries
        changed.append("dependencies (from %s)" % source_key)
    if "name" not in data:
        data["name"] = instance
        changed.append("name")
    if not changed:
        return "already normal (%d dependencies)" % len(entries)
    if not dry_run:
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return "added " + ", ".join(changed) + " (%d dependencies)" % len(entries)


def main():
    work = Path(flag("work", str(paths.WORK_ROOT)))
    dry_run = "--dry-run" in sys.argv
    for path in sorted(work.glob("*/ground_truth.json")):
        instance = path.parent.name
        print("  %-22s %s" % (instance, normalise(path, instance, dry_run)))
        # Keep the copy that ships inside the export in step with it.
        export_copy = path.parent / "metalevel" / "ground_truth.json"
        if export_copy.exists() and not dry_run:
            export_copy.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
