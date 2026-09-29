"""Turns the UniBench-IND dependency diagram into a machine-readable oracle.

The generator ships `doc/inclusion_dependencies_graph-v2.json`, an arrows.app export in which
entities and attributes are nodes, untyped edges mean "entity has attribute", and typed edges are
the dependencies: `#N` for those that always hold, a bare letter for those that do not. Parsing it
beats transcribing the README, and it keeps the oracle traceable to the upstream artifact.

Node ids disambiguate what the captions cannot: two different entities are both captioned "Vendor",
one being the relational table and the other the vendor sub-document nested inside an order item.

Writes unibench_oracle.json next to the generated data.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths

GRAPH = paths.REPO / "upstream" / "unibench-ind" / "doc" / "inclusion_dependencies_graph-v2.json"
OUT = paths.WORK_ROOT / "unibench" / "unibench_oracle.json"

# Node id -> the observation prefix SPIND reports, using each TableSource's displayName:
# Postgres "schema.table", Mongo "database.collection", Neo4j "database:label".
ENTITY = {
    "n52": ("neo4j",    "neo4j:Person"),
    "n56": ("postgres", "public.customer"),
    "n59": ("mongo",    "unibench.Order"),
    "n62": ("postgres", "public.invoice"),
    "n65": ("mongo",    "unibench.Order.order_item"),
    "n66": ("mongo",    "unibench.Order.order_item.vendor"),
    "n69": ("postgres", "public.vendor"),
    "n72": ("neo4j",    "neo4j:Tag"),
}

# Captions carry role annotations that are not part of the column name.
CLEAN = re.compile(r"\s*\((pk|fk|optional|optinal)\)\s*$")


def main():
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in graph["nodes"]}

    owner = {}
    for rel in graph["relationships"]:
        if not rel.get("type"):
            owner[rel["toId"]] = rel["fromId"]

    def qualified(attr_id):
        entity_id = owner.get(attr_id)
        if entity_id not in ENTITY:
            return None, None
        model, prefix = ENTITY[entity_id]
        return model, f"{prefix}.{CLEAN.sub('', nodes[attr_id]['caption'])}"

    dependencies = []
    for rel in graph["relationships"]:
        label = rel.get("type")
        if not label:
            continue
        dep_model, dependent = qualified(rel["fromId"])
        ref_model, referenced = qualified(rel["toId"])
        if dependent is None or referenced is None:
            continue
        dependencies.append({
            "label": label,
            # A numbered edge is asserted to hold on every generated instance; a lettered one is
            # asserted only to hold sometimes, which is the partial case.
            "always_holds": label.startswith("#"),
            "dependent": dependent,
            "referenced": referenced,
            "models": f"{dep_model} -> {ref_model}",
            "cross_model": dep_model != ref_model,
        })

    dependencies.sort(key=lambda d: (not d["always_holds"], d["label"]))
    always = [d for d in dependencies if d["always_holds"]]
    partial = [d for d in dependencies if not d["always_holds"]]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "upstream/unibench-ind doc/inclusion_dependencies_graph-v2.json",
        "always_holds": len(always),
        "does_not_always_hold": len(partial),
        "cross_model": sum(1 for d in dependencies if d["cross_model"]),
        "dependencies": dependencies,
    }, indent=2), encoding="utf-8")

    print(f"{len(dependencies)} dependencies "
          f"({len(always)} always hold, {len(partial)} do not always hold, "
          f"{sum(1 for d in dependencies if d['cross_model'])} cross-model)")
    for d in dependencies:
        mark = "always" if d["always_holds"] else "  ~   "
        print(f"  {d['label']:>4} {mark}  {d['dependent']:<44} -> {d['referenced']}")
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    main()
