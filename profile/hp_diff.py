"""Differences between two Heterogeneity Profiles.

Records are matched by id, which is computed from kind, subject and evidence but not from the
measured values. Three kinds of difference:

  added     a finding only the later profile has
  removed   a finding only the earlier profile has (repaired, or no longer discovered)
  changed   same finding, measurement moved by more than --tolerance

usage: hp_diff.py <before.jsonl> <after.jsonl> [--quadrant=A.23] [--kind=partial-link]
                  [--changed-only] [--json=<out>]
"""
import json
import sys
from pathlib import Path


def flag(name, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith(f"--{name}=")), default)


def read(path):
    return {entry["id"]: entry
            for entry in (json.loads(line) for line in
                          Path(path).read_text(encoding="utf-8").splitlines() if line.strip())}


def subject_of(entry):
    subject = entry["subject"]
    if "dependent" in subject:
        return f"{subject['dependent']} -> {subject['referenced']}"
    return subject.get("extractor", "?")


def moved(before, after, tolerance):
    """The measured fields that differ by more than the tolerance."""
    changes = []
    for field in ("strength", "genuineness", "gcov"):
        old, new = before.get(field), after.get(field)
        if old is None or new is None:
            continue
        if abs(old - new) > tolerance:
            changes.append((field, old, new))
    return changes


def main():
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    before, after = read(positional[0]), read(positional[1])
    tolerance = float(flag("tolerance", 1e-6))
    quadrant, kind = flag("quadrant"), flag("kind")

    def keep(entry):
        return ((quadrant is None or entry.get("cell") == quadrant)
                and (kind is None or entry["kind"] == kind))

    added = [e for i, e in after.items() if i not in before and keep(e)]
    removed = [e for i, e in before.items() if i not in after and keep(e)]
    changed = [(before[i], e) for i, e in after.items()
               if i in before and keep(e) and moved(before[i], e, tolerance)]

    print(f"{Path(positional[0]).name} -> {Path(positional[1]).name}")
    print(f"  {len(before):,} records before, {len(after):,} after; "
          f"{len(added)} added, {len(removed)} removed, {len(changed)} changed\n")

    stable = len(set(before) & set(after))
    print(f"  {stable:,} findings are the same finding in both "
          f"({stable / max(len(before), 1):.0%} of the earlier profile)\n")

    for label, entries in (("added", added), ("removed", removed)):
        if not entries or "--changed-only" in sys.argv[1:]:
            continue
        print(f"  {label} ({len(entries)}):")
        by_cell = {}
        for entry in entries:
            by_cell.setdefault(entry.get("cell") or "-", []).append(entry)
        for cell in sorted(by_cell):
            group = by_cell[cell]
            print(f"    {cell:<5} {len(group):>4}  "
                  + ", ".join(sorted({e['kind'] for e in group})))
            for entry in sorted(group, key=lambda e: subject_of(e))[:6]:
                print(f"      {entry['kind']:<22} {subject_of(entry)}")
            if len(group) > 6:
                print(f"      ... and {len(group) - 6} more")
        print()

    if changed:
        print(f"  changed ({len(changed)}):")
        for old, new in sorted(changed, key=lambda p: subject_of(p[1]))[:20]:
            deltas = ", ".join(f"{field} {a:.4f} -> {b:.4f}"
                               for field, a, b in moved(old, new, tolerance))
            print(f"    {new['cell'] or '-':<5} {new['kind']:<22} {subject_of(new)}")
            print(f"          {deltas}")
        if len(changed) > 20:
            print(f"    ... and {len(changed) - 20} more")

    out = flag("json")
    if out:
        Path(out).write_text(json.dumps(
            {"added": added, "removed": removed,
             "changed": [{"id": n["id"], "kind": n["kind"], "cell": n.get("cell"),
                          "subject": subject_of(n),
                          "moved": [{"field": f, "before": a, "after": b}
                                    for f, a, b in moved(o, n, tolerance)]}
                         for o, n in changed]}, indent=2), encoding="utf-8")
        print(f"\n  written to {out}")


if __name__ == "__main__":
    main()
