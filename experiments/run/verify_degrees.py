"""Recomputes every reported partial degree from the export it was discovered on.

Recomputes bag coverage, the semantics SPIND reports, for every pIND in a result directory from
the exported observation sets, and lists the ones that disagree. On instances with relations of
more than a million rows SPIND can understate a degree (never overstate it).

`--fix` writes the recomputed degrees back into the result file. It cannot restore a dependency
that was filtered out, so discover below the target threshold, fix, and filter afterwards.

A vacuous row is one whose dependent observation is empty, so coverage is undefined.

usage: verify_degrees.py <exportDir> <resultDir> [--tolerance=5e-4] [--quiet] [--fix]
"""
import csv
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

TRAILING_COMMA = re.compile(r",\s*([}\]])")
csv.field_size_limit(1 << 24)


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


class Export:
    """Reads each export file once and serves every column of it from that read.

    Memory is bounded by the number of cached values (an approximate budget), and whole files are
    evicted least-recently-used when it is exceeded.
    """

    def __init__(self, directory, budget=8_000_000):
        self.directory = Path(directory)
        self.catalog = json.loads(
            (self.directory / "catalog.json").read_text(encoding="utf-8"))
        self.by_relation = {entry["relation"]: entry for entry in self.catalog}
        self.budget = budget
        self.files = OrderedDict()   # relation -> {column: [values]}
        self.domains = {}            # (relation, column) -> frozenset of non-null values
        self.supports = {}           # (relation, column) -> (Counter of non-null values, total)
        self.held = 0                # cached values, for the eviction budget

    def _file(self, relation):
        """Every column of one export file, read in a single pass and cached as a unit."""
        if relation in self.files:
            self.files.move_to_end(relation)
            return self.files[relation]
        entry = self.by_relation.get(relation)
        if entry is None:
            return None
        with (self.directory / entry["file"]).open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            columns = [[] for _ in header]
            for row in reader:
                for i, value in enumerate(row):
                    if i < len(header):
                        columns[i].append(value)
        loaded = {name: columns[i] for i, name in enumerate(header)}
        size = sum(len(values) for values in loaded.values())
        # Evict before inserting, and never evict the file just read, so a single file larger
        # than the budget can still be served.
        while self.held + size > self.budget and self.files:
            _, dropped = self.files.popitem(last=False)
            self.held -= sum(len(values) for values in dropped.values())
        self.files[relation] = loaded
        self.held += size
        return loaded

    def column(self, relation, name):
        loaded = self._file(relation)
        if loaded is None:
            return None
        return loaded.get(name)

    def domain(self, relation, name):
        """The referenced side as a set, built once however many candidates reference it.

        Kept separately from the raw columns and never evicted, since it is much smaller than the
        column and reused across candidates.
        """
        key = (relation, name)
        if key not in self.domains:
            values = self.column(relation, name)
            if values is None:
                return None
            self.domains[key] = frozenset(values) - {""}
        return self.domains[key]

    def support(self, relation, name):
        """The dependent side as (value -> multiplicity, total), which is all bag coverage needs.

        Bag coverage is the sum of mult(v) over values inside the referenced support, divided by
        the total, so each distinct value is visited once.
        """
        key = (relation, name)
        if key not in self.supports:
            values = self.column(relation, name)
            if values is None:
                return None
            counts = Counter(v for v in values if v != "")
            self.supports[key] = (counts, sum(counts.values()))
        return self.supports[key]


def reported(result_dir):
    path = Path(result_dir) / "1-ary_pINDs.json"
    text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
    for relation in json.loads(text):
        for pind in relation.get("pINDs", []):
            for ref in pind["referenced"]:
                yield (relation["relation"], ".".join(pind["dependant"]),
                       ref["relation"], ".".join(ref["attribute"]), ref["partialDegree"])


def rewrite(export, result_dir, tolerance):
    """Replaces every reported degree with the coverage recomputed from the export.

    The file is rewritten in place after a `.orig` copy is kept, so a corrected run stays
    distinguishable from one that was right to begin with.
    """
    path = Path(result_dir) / "1-ary_pINDs.json"
    original = path.with_suffix(".json.orig")
    text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
    data = json.loads(text)
    if not original.exists():
        original.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    changed = 0
    for relation in data:
        for pind in relation.get("pINDs", []):
            support = export.support(relation["relation"], ".".join(pind["dependant"]))
            for ref in pind["referenced"]:
                referenced = export.domain(ref["relation"], ".".join(ref["attribute"]))
                if support is None or referenced is None or not support[1]:
                    continue
                counts, total = support
                coverage = sum(n for v, n in counts.items() if v in referenced) / total
                if abs(coverage - ref["partialDegree"]) > tolerance:
                    ref["partialDegree"] = coverage
                    changed += 1
    # Written beside the target and renamed, so an interrupted run leaves the previous file intact.
    scratch = path.with_suffix(".json.partial")
    scratch.write_text(json.dumps(data), encoding="utf-8")
    scratch.replace(path)
    return changed, original


def main():
    export = Export(sys.argv[1])
    result_dir = sys.argv[2]
    tolerance = float(flag("tolerance", "5e-4"))
    quiet = "--quiet" in sys.argv

    matching = mismatching = vacuous = missing = 0
    worst = []
    # Sorted so that one export file serves every candidate drawn from it before it is evicted.
    for dep_rel, dep_col, ref_rel, ref_col, degree in sorted(reported(result_dir)):
        support = export.support(dep_rel, dep_col)
        referenced = export.domain(ref_rel, ref_col)
        if support is None or referenced is None:
            missing += 1
            continue
        counts, total = support
        if not total:
            # An empty dependent observation is contained in everything; coverage is undefined.
            vacuous += 1
            continue
        coverage = sum(n for v, n in counts.items() if v in referenced) / total
        if abs(coverage - degree) <= tolerance:
            matching += 1
        else:
            mismatching += 1
            worst.append((degree - coverage, dep_rel + "." + dep_col,
                          ref_rel + "." + ref_col, degree, coverage))

    print("%s against %s" % (result_dir, sys.argv[1]))
    print("  %d degrees match, %d disagree, %d vacuous, %d name not in export"
          % (matching, mismatching, vacuous, missing))
    if worst and not quiet:
        worst.sort(key=lambda row: row[0])
        print("  %-46s %-40s %9s %9s" % ("dependent", "referenced", "reported", "recomputed"))
        for _, left, right, degree, coverage in worst[:20]:
            print("  %-46s %-40s %9.4f %9.4f" % (left, right, degree, coverage))
    if "--fix" in sys.argv and mismatching:
        changed, original = rewrite(export, result_dir, tolerance)
        print("  rewrote %d degrees; the original is kept at %s" % (changed, original.name))
        return 0
    return 1 if mismatching else 0


if __name__ == "__main__":
    sys.exit(main())
