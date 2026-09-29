"""Inclusions that hold because of how a scope was built, rather than because of the data.

A union scope contains its members by construction, so for every member scope `L` and every
extractor the union carries, `L.x` is included in `union.x` on any instance. Union membership is
read from the catalog's `members` list (written by `MetaLevelExporter`); without it nothing is
suppressed.
"""
import json
from pathlib import Path


def entailed_pairs(export):
    """The (dependent, referenced) extractor pairs a union's construction already guarantees.

    Returns pairs in the same `relation.attribute` spelling the result files use, so a caller can
    subtract them from a discovered set directly.
    """
    catalog_path = Path(export) / "catalog.json"
    if not catalog_path.exists():
        return set()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    columns_of = {}
    for entry in catalog:
        columns_of.setdefault(entry["relation"], entry)

    pairs = set()
    for entry in catalog:
        members = entry.get("members") or []
        if not members:
            continue
        # Only the union's own columns are entailed. A discovered union (discover_scopes.py) may map
        # them from differently named member columns, recorded in `member_columns`.
        renamed = entry.get("member_columns") or {}
        for member in members:
            if member not in columns_of:
                continue
            for column in _column_names(export, entry):
                pairs.add((member + "." + renamed.get(member, column), entry["relation"] + "." + column))
    return pairs


def _column_names(export, entry):
    """The header of one exported scope. Read from the file, since the catalog stores only a count."""
    path = Path(export) / entry["file"]
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        header = handle.readline().rstrip("\r\n")
    return [name.strip('"') for name in header.split(",") if name]
