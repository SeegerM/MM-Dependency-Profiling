"""Writes the oracle file for each publisher-assembled instance.

These publishers document their correspondences in prose, snapshot summaries or the structure
of the data rather than as a file, so the oracles are written down here; each entry records its
evidence. The hand-written oracles in experiments/datasets/oracles/ are installed the same way.

usage: published_oracles.py [--work=$WORK_ROOT]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402

HERE = Path(__file__).resolve().parents[2]
ORACLE_FILES = Path(__file__).resolve().parent / "oracles"

ORACLES = {
    "movielens": {
        "source": "MovieLens 32M links.csv, TMDB daily export, Wikidata P345/P4947 slice",
        "name": "movielens",
        "dependencies": [
            {"dependent": "movielens.links.tmdbid", "referenced": "tmdb.movies.id",
             "models": "relational -> document", "cross_model": True,
             "evidence": "links.csv maps every film to a TMDB identifier"},
            {"dependent": "movielens.links.movieid", "referenced": "movielens.movies.movieid",
             "models": "relational -> relational", "cross_model": False,
             "evidence": "links and movies are keyed on the same identifier"},
            {"dependent": "movielens.ratings.movieid", "referenced": "movielens.movies.movieid",
             "models": "relational -> relational", "cross_model": False,
             "evidence": "a rating is of a film in the catalogue"},
            {"dependent": "movielens.tags.movieid", "referenced": "movielens.movies.movieid",
             "models": "relational -> relational", "cross_model": False,
             "evidence": "a tag is on a film in the catalogue"},
            {"dependent": "neo4j:WikidataFilm@movielens_movie_ids.movielens_movie_ids",
             "referenced": "movielens.movies.movieid",
             "models": "graph list -> relational", "cross_model": True,
             "evidence": "the slice was extracted from the MovieLens population"},
            {"dependent": "neo4j:WikidataFilm.wikidata_tmdb_id",
             "referenced": "movielens.links.tmdbid",
             "models": "graph -> relational", "cross_model": True,
             "evidence": "both publishers record a TMDB identifier for the same film"},
            {"dependent": "movielens.movies.movieid",
             "referenced": "neo4j:WikidataFilm@movielens_movie_ids.movielens_movie_ids",
             "models": "relational -> graph list", "cross_model": True,
             "evidence": "the snapshot reports 87.12% of the population mapped"},
            # Not reachable under equality: the two publishers write the IMDb identifier differently.
            {"dependent": "movielens.links.imdbid", "referenced": "neo4j:WikidataFilm.imdb_id",
             "models": "relational -> graph", "cross_model": True,
             "evidence": "both publishers record the film's IMDb identifier, written differently",
             "needs_comparison_relation": "registry-prefix, or edit distance 2"},
            # From recover_acquired_keys.py (results/acquired-keys.json): the Wikidata-to-MovieLens direction of the
            # IMDb link, which is exact where ours is partial because the slice is bounded.
            {"dependent": "neo4j:WikidataFilm.imdb_id", "referenced": "movielens.links.imdbid",
             "models": "graph -> relational", "cross_model": True,
             "evidence": "every mapped Wikidata film is a MovieLens film (1.0)",
             "documented_coverage": 1.0,
             "needs_comparison_relation": "registry-prefix, or edit distance 2"},
            {"dependent": "neo4j:WikidataFilm.wikidata_tmdb_id", "referenced": "tmdb.movies.id",
             "models": "graph -> document", "cross_model": True,
             "evidence": "Wikidata's TMDB identifiers are TMDB identifiers (0.998443)",
             "documented_coverage": 0.99844288},
        ],
    },
    # MMSBench-Net ships no key or foreign-key oracle. These entries come from its schema and
    # generator, checked against the exports by recover_mmsbench_keys.py. The generator writes
    # both an array `users` and an optional nested scalar `user.id`, hence two user references.
    "coms": {
        "source": "MMSBench-Net (Polypheny coms) schema and generator, audited by "
                  "experiments/datasets/recover_mmsbench_keys.py",
        "name": "coms",
        "dependencies": [
            {"dependent": "coms.login.userid", "referenced": "coms.user.id",
             "models": "relational -> relational", "cross_model": False,
             "evidence": "the generator writes a login for an existing user (15,000/15,000)"},
            {"dependent": "coms.logs@users.users", "referenced": "coms.user.id",
             "models": "document array -> relational", "cross_model": True,
             "evidence": "the log's user array draws from User.idBuilder (366/366)"},
            {"dependent": "coms.logs.user.id", "referenced": "coms.user.id",
             "models": "nested document -> relational", "cross_model": True,
             "evidence": "the optional nested scalar draws from the same domain (94/94)"},
            # The device references and edge endpoints target the disjoint union of six node
            # labels, which is not one exported scope, so they are marked as union targets.
            {"dependent": "coms.login.deviceid", "referenced": "coms.device-union.id",
             "models": "relational -> graph", "cross_model": True, "union_target": True,
             "evidence": "every login names one of the 165 devices (15,000/15,000)"},
            {"dependent": "coms.logs.deviceid", "referenced": "coms.device-union.id",
             "models": "document -> graph", "cross_model": True, "union_target": True,
             "evidence": "every log names one of the 165 devices (165/165)"},
            {"dependent": "neo4j:-[coms_lan]->.source.id", "referenced": "coms.device-union.id",
             "models": "graph edge -> graph", "cross_model": False, "union_target": True,
             "evidence": "a LAN edge starts at a server or switch (26/26)"},
            {"dependent": "neo4j:-[coms_lan]->.target.id", "referenced": "coms.device-union.id",
             "models": "graph edge -> graph", "cross_model": False, "union_target": True,
             "evidence": "a LAN edge ends at a server, switch, PC or AP (26/26)"},
            {"dependent": "neo4j:-[coms_wlan]->.source.id", "referenced": "coms.device-union.id",
             "models": "graph edge -> graph", "cross_model": False, "union_target": True,
             "evidence": "a WLAN edge starts at an access point (101/101)"},
            {"dependent": "neo4j:-[coms_wlan]->.target.id", "referenced": "coms.device-union.id",
             "models": "graph edge -> graph", "cross_model": False, "union_target": True,
             "evidence": "a WLAN edge ends at a mobile or IoT device (101/101)"},
        ],
    },
    "hpatent": {
        "source": "Helsinki UDBMS patent dataset: patent/inventor/assignee/class tables "
                  "and the citation edge list",
        "name": "hpatent",
        "dependencies": [
            {"dependent": "neo4j:-[CITES]->.source.id", "referenced": "hpatent.patent.patent",
             "models": "graph edge -> relational", "cross_model": True,
             "evidence": "a citing patent is one of the corpus's patents"},
            {"dependent": "neo4j:-[CITES]->.target.id", "referenced": "hpatent.patent.patent",
             "models": "graph edge -> relational", "cross_model": True,
             "evidence": "a cited patent may predate the table, so this is partial by design"},
            {"dependent": "neo4j:HPatent.id", "referenced": "hpatent.patent.patent",
             "models": "graph -> relational", "cross_model": True,
             "evidence": "nodes exist for every identifier the edge list names"},
            {"dependent": "hpatent.inventor.patent", "referenced": "hpatent.patent.patent",
             "models": "relational -> relational", "cross_model": False,
             "evidence": "an inventor row names the patent it belongs to"},
            # Partial: three rows name assignee identifiers missing from the assignee table
            # (703089, 722814).
            {"dependent": "hpatent.patent.assignee", "referenced": "hpatent.assignee.assignee",
             "models": "relational -> relational", "cross_model": False,
             "evidence": "the assignee column names a row of the assignee table, on all but "
                         "three rows (0.999999)"},
            # Holds only once class.table's 76 unquoted-comma rows are rejoined rather than
            # skipped; see build_helsinki_patent.py.
            {"dependent": "hpatent.patent.nclass", "referenced": "hpatent.class.class",
             "models": "relational -> relational", "cross_model": False,
             "evidence": "every patent carries a classification the class table defines"},
            {"dependent": "hpatent.class.subcat", "referenced": "hpatent.category.subcat",
             "models": "relational -> relational", "cross_model": False,
             "evidence": "the classification tables are a hierarchy"},
            # From recover_acquired_keys.py (results/acquired-keys.json). Both are n-ary, so a unary report can exclude them
            # from its denominator.
            {"dependent": "hpatent.patent.(cat,subcat)",
             "referenced": "hpatent.category.(cat,subcat)",
             "models": "relational -> relational", "cross_model": False, "arity": 2,
             "evidence": "every patent's category pair is one the category table defines (1.0)",
             "documented_coverage": 1.0},
            {"dependent": "hpatent.class.(cat,subcat)",
             "referenced": "hpatent.category.(cat,subcat)",
             "models": "relational -> relational", "cross_model": False, "arity": 2,
             "evidence": "every classification's category pair is defined (1.0)",
             "documented_coverage": 1.0},
        ],
    },
    "listenbrainz": {
        "source": "ListenBrainz sample dump (popularity, artist cache) and incremental "
                  "listen events",
        "name": "listenbrainz",
        "dependencies": [
            {"dependent": "listenbrainz.top_recording.artist_mbid",
             "referenced": "neo4j:LBArtist.artist_mbid",
             "models": "relational -> graph", "cross_model": True,
             "evidence": "the cache covers the artists the popularity tables rank"},
            {"dependent": "listenbrainz.mlhd_top_recording.artist_mbid",
             "referenced": "neo4j:LBArtist.artist_mbid",
             "models": "relational -> graph", "cross_model": True,
             "evidence": "as above, for the second popularity table"},
            {"dependent": "neo4j:LBArtist.artist_mbid",
             "referenced": "listenbrainz.top_recording.artist_mbid",
             "models": "graph -> relational", "cross_model": True,
             "evidence": "partial: the cache also holds artists with no popularity row"},
            # From recover_acquired_keys.py (results/acquired-keys.json). Both are far below any usable threshold, because
            # the listen events and popularity tables come from dumps of different scope and date.
            {"dependent": "listenbrainz.listens.track_metadata.additional_info.recording_mbid",
             "referenced": "listenbrainz.top_recording.recording_mbid",
             "models": "document -> relational", "cross_model": True,
             "evidence": "a listened recording may appear in the popularity sample (0.203509)",
             "documented_coverage": 0.20350926},
            {"dependent": "listenbrainz.listens@track_metadata.additional_info.artist_mbids"
                          ".track_metadata.additional_info.artist_mbids",
             "referenced": "listenbrainz.top_recording.artist_mbid",
             "models": "document array -> relational", "cross_model": True,
             "evidence": "a listened artist may appear in the popularity sample (0.062856)",
             "documented_coverage": 0.06285568},
        ],
    },
}


def flag(name, default):
    return next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--" + name + "=")),
                default)


def oracles():
    """The inline oracles above, then the hand-written ones kept as files."""
    yield from ORACLES.items()
    for path in sorted(ORACLE_FILES.glob("*.json")):
        yield path.stem, json.loads(path.read_text(encoding="utf-8"))


def main():
    work = Path(flag("work", str(paths.WORK_ROOT)))
    for directory, oracle in oracles():
        target = work / directory
        if not target.exists():
            print("  skipped %-16s (no instance)" % directory)
            continue
        body = json.dumps(oracle, indent=1)
        written = []
        # Beside the export, so an export directory can be handed to a scorer on its own.
        export = target / "metalevel"
        if export.exists():
            (export / "ground_truth.json").write_text(body, encoding="utf-8")
            written.append("metalevel/ground_truth.json")
        # And an identical copy at the instance root, for scorers that take an instance directory.
        (target / "ground_truth.json").write_text(body, encoding="utf-8")
        written.append("ground_truth.json")
        crossing = sum(1 for d in oracle["dependencies"] if d["cross_model"])
        print("  %-16s %d dependencies, %d crossing a model boundary -> %s"
              % (directory, len(oracle["dependencies"]), crossing, ", ".join(written)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
