"""Stamps every oracle entry with its provenance and arity, so one manifest can drive every table.

Two fields are added to each entry and nothing else changes.

  provenance   'publisher' if one publisher's own artifact states the correspondence (a documented
               file format, a declared schema, a published generator), 'reconstructed' if it was
               established between independently published sources or from the data.

  arity        the number of attribute correspondences, so unary runs can exclude composite
               entries (e.g. Helsinki Patent's (cat, subcat)) from their denominators.

Provenance is assigned explicitly below rather than inferred from evidence strings.

usage: stamp_oracle_provenance.py [--work=$WORK_ROOT] [--dry-run]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402

# instance -> dependents that are 'reconstructed' (None: all of them). Anything unlisted
# defaults to 'publisher'.
RECONSTRUCTED = {
    "movielens": {
        # Wikidata and MovieLens are separate publishers; these correspondences are established
        # from the data rather than stated by either.
        "neo4j:WikidataFilm@movielens_movie_ids.movielens_movie_ids",
        "neo4j:WikidataFilm.wikidata_tmdb_id",
        "movielens.movies.movieid",
        "movielens.links.imdbid",
        "neo4j:WikidataFilm.imdb_id",
    },
    "hpatent": {
        # The citation edge list ships with no declared relationship to the patent table.
        "neo4j:-[CITES]->.source.id",
        "neo4j:-[CITES]->.target.id",
        "neo4j:HPatent.id",
        "hpatent.patent.(cat,subcat)",
        "hpatent.class.(cat,subcat)",
    },
    # ListenBrainz documents no correspondence between its three dumps.
    "listenbrainz": None,
    # MMSBench-Net's correspondences come from its published generator.
    "coms": set(),
}


def flag(name, default=None):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


def arity_of(entry):
    """How many attribute correspondences the entry carries; composites are written t.(a,b).

    Returns an integer, since readers use int(entry["arity"]).
    """
    for side in ("dependent", "referenced"):
        value = entry.get(side)
        if isinstance(value, (list, tuple)):
            return len(value)
        if isinstance(value, str) and "(" in value and "," in value:
            inner = value[value.index("(") + 1:value.rindex(")")]
            return len([a for a in inner.split(",") if a.strip()])
    return 1


def main():
    work = Path(flag("work", str(paths.WORK_ROOT)))
    dry = "--dry-run" in sys.argv[1:]
    for name, reconstructed in RECONSTRUCTED.items():
        path = work / name / "ground_truth.json"
        if not path.exists():
            print("  %-14s no ground_truth.json" % name)
            continue
        blob = json.loads(path.read_text(encoding="utf-8"))
        counts = {}
        for entry in blob["dependencies"]:
            if "dependent" not in entry:
                continue
            if reconstructed is None:
                provenance = "reconstructed"
            else:
                provenance = ("reconstructed" if entry["dependent"] in reconstructed
                              else "publisher")
            entry["provenance"] = provenance
            entry["arity"] = arity_of(entry)
            key = (provenance, "n-ary" if entry["arity"] > 1 else "unary")
            counts[key] = counts.get(key, 0) + 1
        if not dry:
            path.write_text(json.dumps(blob, indent=1) + "\n", encoding="utf-8")
        print("  %-14s %s" % (name, ", ".join("%s/%s=%d" % (p, a, n)
                                              for (p, a), n in sorted(counts.items()))))
    print("stamped" if not dry else "dry run: nothing written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
