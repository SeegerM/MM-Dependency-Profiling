#!/usr/bin/env python3
"""Recover source-designed keys and references for the local MMSBench-Net exports.

MMSBench-Net is the Polypheny ``coms`` scenario.  It publishes no key/foreign-key
oracle, so this audit combines two kinds of evidence without conflating them:

* declarations and construction rules in the upstream scenario; and
* exact uniqueness/inclusion checks over every locally stored export snapshot.

Random ``key0`` ... ``key9`` fields are deliberately not mined for accidental
constraints.  The Mongo ``_id`` added by this project's loader is reported as a
physical key, but never promoted to a benchmark-designed key.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from itertools import chain
from pathlib import Path
from typing import Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402


NULLS = {"", "\\N", "null", "None"}
DEVICE_LABELS = ("ap", "iot", "mobile", "pc", "server", "switch")
EDGE_TYPES = ("lan", "wlan")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def values(rows: Iterable[dict[str, str]], column: str) -> list[str]:
    return [row.get(column, "").strip() for row in rows]


def key_stats(rows: Sequence[dict[str, str]], columns: Sequence[str]) -> dict[str, object]:
    seen: set[tuple[str, ...]] = set()
    non_null = duplicates = 0
    for row in rows:
        key = tuple(row.get(column, "").strip() for column in columns)
        if any(value in NULLS for value in key):
            continue
        non_null += 1
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return {
        "columns": list(columns),
        "rows": len(rows),
        "non_null_rows": non_null,
        "null_rows": len(rows) - non_null,
        "distinct": len(seen),
        "duplicate_rows": duplicates,
        "is_key": non_null == len(rows) and duplicates == 0,
        "is_unique_when_present": duplicates == 0,
    }


def scalar_key_stats(raw_values: Iterable[str], relation: str, column: str) -> dict[str, object]:
    rows = [{column: value} for value in raw_values]
    result = key_stats(rows, (column,))
    result["relation"] = relation
    return result


def inclusion(
    source: Iterable[str], target: set[str], target_is_key: bool = True
) -> dict[str, object]:
    source_values = [value.strip() for value in source if value.strip() not in NULLS]
    missing = sorted(set(source_values) - target)
    matched_rows = sum(value in target for value in source_values)
    distinct_source = set(source_values)
    return {
        "non_null_rows": len(source_values),
        "matched_rows": matched_rows,
        "row_coverage": round(matched_rows / len(source_values), 8) if source_values else None,
        "distinct_source_values": len(distinct_source),
        "matched_distinct_values": len(distinct_source) - len(missing),
        "distinct_coverage": (
            round((len(distinct_source) - len(missing)) / len(distinct_source), 8)
            if distinct_source
            else None
        ),
        "missing_examples": missing[:10],
        "target_is_key": target_is_key,
        "exact_foreign_key_instance": not missing and target_is_key,
    }


def catalog_map(export: Path) -> dict[str, dict[str, object]]:
    entries = json.loads((export / "catalog.json").read_text(encoding="utf-8"))
    return {entry["relation"]: entry for entry in entries}


def relation_rows(export: Path, catalog: dict[str, dict[str, object]], relation: str):
    return read_rows(export / str(catalog[relation]["file"]))


def analyze_export(export: Path) -> dict[str, object]:
    catalog = catalog_map(export)
    user_rows = relation_rows(export, catalog, "coms.user")
    login_rows = relation_rows(export, catalog, "coms.login")
    log_rows = relation_rows(export, catalog, "coms.logs")
    log_user_rows = relation_rows(export, catalog, "coms.logs@users")

    node_rows = {
        label: relation_rows(export, catalog, f"neo4j:coms_{label}")
        for label in DEVICE_LABELS
    }
    edge_rows = {
        kind: relation_rows(export, catalog, f"neo4j:-[coms_{kind}]->")
        for kind in EDGE_TYPES
    }

    user_ids = set(values(user_rows, "id"))
    node_ids_by_label = {label: set(values(rows, "id")) for label, rows in node_rows.items()}
    node_ids = set().union(*node_ids_by_label.values())
    edge_ids_by_type = {kind: set(values(rows, "id")) for kind, rows in edge_rows.items()}
    edge_ids = set().union(*edge_ids_by_type.values())

    declared_keys = {
        "coms.user.[id]": key_stats(user_rows, ("id",)),
        "coms.login.[accesstime]": key_stats(login_rows, ("accesstime",)),
    }
    login_candidate = key_stats(login_rows, ("userid", "deviceid", "accesstime"))
    login_proper_subsets = {
        ",".join(columns): key_stats(login_rows, columns)
        for columns in (
            ("userid",),
            ("deviceid",),
            ("accesstime",),
            ("userid", "deviceid"),
            ("userid", "accesstime"),
            ("deviceid", "accesstime"),
        )
    }
    login_candidate["minimal"] = bool(login_candidate["is_key"]) and not any(
        stats["is_key"] for stats in login_proper_subsets.values()
    )
    login_candidate["proper_subset_validation"] = login_proper_subsets
    recovered_keys = {
        "coms.login.[userid,deviceid,accesstime]": login_candidate,
        "coms.logs.[_id]": key_stats(log_rows, ("_id",)),
        "coms.logs.[deviceid]": key_stats(log_rows, ("deviceid",)),
        "graph.device-union.[id]": scalar_key_stats(
            chain.from_iterable(values(rows, "id") for rows in node_rows.values()),
            "graph.device-union",
            "id",
        ),
        "graph.edge-union.[id]": scalar_key_stats(
            chain.from_iterable(values(rows, "id") for rows in edge_rows.values()),
            "graph.edge-union",
            "id",
        ),
        "graph.edge-union.[source.id,target.id]": key_stats(
            list(chain.from_iterable(edge_rows.values())), ("source.id", "target.id")
        ),
    }
    for label, rows in node_rows.items():
        recovered_keys[f"neo4j:coms_{label}.[id]"] = key_stats(rows, ("id",))
    for kind, rows in edge_rows.items():
        recovered_keys[f"neo4j:-[coms_{kind}]->.[id]"] = key_stats(rows, ("id",))
        recovered_keys[
            f"neo4j:-[coms_{kind}]->.[source.id,target.id]"
        ] = key_stats(rows, ("source.id", "target.id"))

    references: dict[str, dict[str, object]] = {
        "coms.login.userid -> coms.user.id": inclusion(
            values(login_rows, "userid"), user_ids, declared_keys["coms.user.[id]"]["is_key"]
        ),
        "coms.logs@users.users -> coms.user.id": inclusion(
            values(log_user_rows, "users"), user_ids, declared_keys["coms.user.[id]"]["is_key"]
        ),
        "coms.logs.user.id -> coms.user.id": inclusion(
            values(log_rows, "user.id"), user_ids, declared_keys["coms.user.[id]"]["is_key"]
        ),
        "coms.login.deviceid -> graph.device-union.id": inclusion(
            values(login_rows, "deviceid"), node_ids,
            recovered_keys["graph.device-union.[id]"]["is_key"],
        ),
        "coms.logs.deviceid -> graph.device-union.id": inclusion(
            values(log_rows, "deviceid"), node_ids,
            recovered_keys["graph.device-union.[id]"]["is_key"],
        ),
    }

    typed_domains = {
        "lan": {
            "source": node_ids_by_label["server"] | node_ids_by_label["switch"],
            "target": (
                node_ids_by_label["server"]
                | node_ids_by_label["switch"]
                | node_ids_by_label["pc"]
                | node_ids_by_label["ap"]
            ),
            "source_labels": ["server", "switch"],
            "target_labels": ["server", "switch", "pc", "ap"],
        },
        "wlan": {
            "source": node_ids_by_label["ap"],
            "target": node_ids_by_label["mobile"] | node_ids_by_label["iot"],
            "source_labels": ["ap"],
            "target_labels": ["mobile", "iot"],
        },
    }
    endpoint_references: dict[str, dict[str, object]] = {}
    for kind, rows in edge_rows.items():
        for endpoint in ("source", "target"):
            broad_name = f"neo4j:-[coms_{kind}]->.{endpoint}.id -> graph.device-union.id"
            endpoint_references[broad_name] = inclusion(
                values(rows, f"{endpoint}.id"), node_ids,
                recovered_keys["graph.device-union.[id]"]["is_key"],
            )
            typed_name = (
                f"neo4j:-[coms_{kind}]->.{endpoint}.id -> graph."
                + "|".join(typed_domains[kind][f"{endpoint}_labels"])
                + ".id"
            )
            endpoint_references[typed_name] = inclusion(
                values(rows, f"{endpoint}.id"), typed_domains[kind][endpoint], True
            )

    graph_element_ids = node_ids | edge_ids
    graph_element_count = sum(len(rows) for rows in node_rows.values()) + sum(
        len(rows) for rows in edge_rows.values()
    )
    return {
        "path": str(export),
        "catalog": {
            "relations": len(catalog),
            "rows": sum(int(entry["rows"]) for entry in catalog.values()),
            "columns": sum(int(entry["columns"]) for entry in catalog.values()),
            "core_rows": {
                "user": len(user_rows),
                "login": len(login_rows),
                "logs": len(log_rows),
                "logs@users": len(log_user_rows),
                "nodes": sum(len(rows) for rows in node_rows.values()),
                "edges": sum(len(rows) for rows in edge_rows.values()),
            },
        },
        "declared_key_validation": declared_keys,
        "recovered_key_validation": recovered_keys,
        "global_graph_element_identity": {
            "rows": graph_element_count,
            "distinct_ids": len(graph_element_ids),
            "node_edge_id_overlap": len(node_ids & edge_ids),
            "is_key": len(graph_element_ids) == graph_element_count,
        },
        "designed_data_references": references,
        "graph_endpoint_references": endpoint_references,
    }


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=paths.WORK_ROOT)
    parser.add_argument(
        "--output", type=Path, default=root / "results" / "mmsbench-net-keys.json"
    )
    parser.add_argument(
        "--instances", nargs="*", default=["coms", "coms-iso"],
        help="instance directories below --work",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    snapshots: dict[str, object] = {}
    for name in args.instances:
        export = (args.work / name / "metalevel").resolve()
        if not (export / "catalog.json").exists():
            print(f"Skipping {name}: no {export / 'catalog.json'}", file=sys.stderr)
            continue
        print(f"Scanning {name}...", flush=True)
        snapshots[name] = analyze_export(export)
    if not snapshots:
        print("No MMSBench-Net exports found", file=sys.stderr)
        return 1

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": "MMSBench-Net (Polypheny Simple Client 'coms' scenario)",
        "ground_truth_status": "source-code-derived; the benchmark ships no FK/IND oracle",
        "methodology": {
            "declared_key": "PRIMARY KEY emitted by the upstream schema generator",
            "recovered_key": "source-defined identity or non-null uniqueness in the stored snapshot",
            "foreign_key": "source construction links the domains and every stored dependent value exists in a unique target domain",
            "excluded": "random key0..key9 overlaps and loader-created identities are not semantic foreign keys",
        },
        "source_schema": {
            "declared_primary_keys": ["coms.user.[id]", "coms.login.[accesstime]"],
            "declared_foreign_keys": [],
            "document_schema": "schemaless logs collection; no publisher-defined document key",
            "graph_schema": "bare property-graph database; GraphElement.i is the source identity (exported locally as id)",
        },
        "source_designed_references": [
            "coms.login.userid -> coms.user.id",
            "coms.logs@users.users -> coms.user.id",
            "coms.logs.user.id -> coms.user.id",
            "coms.login.deviceid -> graph.device-union.id",
            "coms.logs.deviceid -> graph.device-union.id",
        ],
        "source_evidence": {
            "keys": "upstream/.../simulation/User.java:58-77 and GraphElement.java:52",
            "schema": "upstream/.../simulation/entites/Graph.java:239-331",
            "login_references": "upstream/.../simulation/NetworkGenerator.java:175-185",
            "document_user_references": "upstream/.../simulation/NetworkGenerator.java:348-360",
            "document_device_reference": "upstream/.../simulation/entites/Graph.java:348-356",
            "edge_endpoints": "upstream/.../simulation/entites/Graph.java:88-117",
        },
        "snapshots": snapshots,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
