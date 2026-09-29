"""Converts the LDBC Social Network Benchmark (CsvBasic) into a multi-model instance with a union oracle.

LDBC's schema declares three supertypes whose subtypes are separate node types in its graph
model: Place = City | Country | Continent, Organisation = University | Company and
Message = Post | Comment. Their identifiers come from one id space per supertype, so the
subtypes are disjoint populations, and several of LDBC's own relationships reference a supertype
rather than one subtype: an organisation is located in a city (universities) or a country
(companies), a place is part of a country (cities) or a continent (countries). With one graph
label per subtype, those references can only be stated against a union scope.

Store assignment (ours, as for every converted corpus):

  graph       City, Country, Continent, University, Company, Tag, TagClass, and the relationships
              among them (IS_LOCATED_IN for organisations, IS_PART_OF, HAS_TYPE, IS_SUBCLASS_OF)
  document    posts and comments, each with its tags as an array scope; creator, place, forum
              and reply-of references as fields
  relational  persons, forums, and the person/forum relationship tables

The oracle (ground_truth.json) lists every reference with its documented target. A reference whose
target is a supertype names the supertype's union (`ldbc.place-union`, `ldbc.organisation-union`);
the union declarations record the member labels. Message is recorded as a supertype that no
reference demands, since CsvBasic splits replies and likes by target type.

usage: build_ldbc.py [--source=upstream/ldbc-snb/data/social_network-sf0.1-CsvBasic-LongDateFormatter]
                     [--out=$WORK_ROOT/ldbc]
"""
import csv
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402

DEFAULT_SOURCE = paths.REPO / "upstream" / "ldbc-snb" / "data" / \
    "social_network-sf0.1-CsvBasic-LongDateFormatter"


def flag(name, default=None):
    prefix = f"--{name}="
    return next((a[len(prefix):] for a in sys.argv[1:] if a.startswith(prefix)), default)


def read(source, name):
    """Rows of one CsvBasic file, located in static/ or dynamic/."""
    for part in ("static", "dynamic"):
        path = source / part / f"{name}_0_0.csv"
        if path.exists():
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.reader(handle, delimiter="|")
                return next(reader), list(reader)
    raise SystemExit(f"{name}_0_0.csv not found below {source}")


class Export:
    def __init__(self, out):
        self.out, self.catalog = out, []

    def scope(self, relation, store, columns, rows, scope="base"):
        file = relation.replace(":", "_").replace("[", "_").replace("]", "_").replace(">", "_") + ".csv"
        with (self.out / file).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)
        self.catalog.append({"relation": relation, "store": store, "scope": scope,
                             "rows": len(rows), "columns": len(columns), "file": file})

    def close(self):
        (self.out / "catalog.json").write_text(json.dumps(self.catalog, indent=1), encoding="utf-8")


def main():
    source = Path(flag("source", str(DEFAULT_SOURCE)))
    out = Path(flag("out", str(paths.WORK_ROOT / "ldbc")))
    export_dir = out / "metalevel"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_dir.mkdir(parents=True)
    ex = Export(export_dir)

    # --- graph: supertypes split into their LDBC subtypes
    _, places = read(source, "place")
    place_type = {}
    by_type = defaultdict(list)
    for pid, name, url, kind in places:
        place_type[pid] = kind
        by_type[kind].append([pid, name, url])
    for kind, label in (("city", "City"), ("country", "Country"), ("continent", "Continent")):
        ex.scope(f"neo4j:{label}", "graph", ["id", "name", "url"], by_type[kind])

    _, orgs = read(source, "organisation")
    org_by_type = defaultdict(list)
    for oid, kind, name, url in orgs:
        org_by_type[kind].append([oid, name, url])
    for kind, label in (("university", "University"), ("company", "Company")):
        ex.scope(f"neo4j:{label}", "graph", ["id", "name", "url"], org_by_type[kind])

    _, tags = read(source, "tag")
    ex.scope("neo4j:Tag", "graph", ["id", "name", "url"], tags)
    _, classes = read(source, "tagclass")
    ex.scope("neo4j:TagClass", "graph", ["id", "name", "url"], classes)

    for name, edge in (("organisation_isLocatedIn_place", "IS_LOCATED_IN"),
                       ("place_isPartOf_place", "IS_PART_OF"),
                       ("tag_hasType_tagclass", "HAS_TYPE"),
                       ("tagclass_isSubclassOf_tagclass", "IS_SUBCLASS_OF")):
        _, rows = read(source, name)
        ex.scope(f"neo4j:-[{edge}]->", "graph", ["source.id", "target.id"], [r[:2] for r in rows])

    # --- documents: posts and comments with their references as fields, tags as arrays
    def single(name):
        _, rows = read(source, name)
        return {r[0]: r[1] for r in rows}

    def multi(name):
        _, rows = read(source, name)
        out_map = defaultdict(list)
        for r in rows:
            out_map[r[0]].append(r[1])
        return out_map

    post_creator, post_place = single("post_hasCreator_person"), single("post_isLocatedIn_place")
    post_forum = {post: forum for forum, post in (r[:2] for r in read(source, "forum_containerOf_post")[1])}
    post_tags = multi("post_hasTag_tag")
    header, posts = read(source, "post")
    base = ["id", "creationDate", "locationIP", "browserUsed", "language", "content", "length"]
    pos = [header.index(c) for c in base]
    ex.scope("social.post", "document", base + ["creator", "place", "forum"],
             [[p[i] for i in pos] + [post_creator.get(p[0], ""), post_place.get(p[0], ""),
                                     post_forum.get(p[0], "")] for p in posts])
    ex.scope("social.post@tags", "document", ["tags"],
             [[t] for p in posts for t in post_tags.get(p[0], [])], scope="tags")

    c_creator, c_place = single("comment_hasCreator_person"), single("comment_isLocatedIn_place")
    c_post, c_comment = single("comment_replyOf_post"), single("comment_replyOf_comment")
    c_tags = multi("comment_hasTag_tag")
    header, comments = read(source, "comment")
    base = ["id", "creationDate", "locationIP", "browserUsed", "content", "length"]
    pos = [header.index(c) for c in base]
    ex.scope("social.comment", "document", base + ["creator", "place", "replyOfPost", "replyOfComment"],
             [[c[i] for i in pos] + [c_creator.get(c[0], ""), c_place.get(c[0], ""),
                                     c_post.get(c[0], ""), c_comment.get(c[0], "")] for c in comments])
    ex.scope("social.comment@tags", "document", ["tags"],
             [[t] for c in comments for t in c_tags.get(c[0], [])], scope="tags")

    # --- relational: persons, forums, relationship tables
    person_place = single("person_isLocatedIn_place")
    header, persons = read(source, "person")
    base = ["id", "firstName", "lastName", "gender", "birthday", "creationDate", "locationIP", "browserUsed"]
    pos = [header.index(c) for c in base]
    ex.scope("pg.person", "relational", base + ["place"],
             [[p[i] for i in pos] + [person_place.get(p[0], "")] for p in persons])
    moderator = single("forum_hasModerator_person")
    header, forums = read(source, "forum")
    ex.scope("pg.forum", "relational", ["id", "title", "creationDate", "moderator"],
             [[f[0], f[1], f[2], moderator.get(f[0], "")] for f in forums])
    for name, relation, columns in (
            ("person_knows_person", "pg.person_knows_person", ["person1", "person2"]),
            ("person_likes_post", "pg.person_likes_post", ["person", "post"]),
            ("person_likes_comment", "pg.person_likes_comment", ["person", "comment"]),
            ("person_studyAt_organisation", "pg.person_study_at", ["person", "university", "classYear"]),
            ("person_workAt_organisation", "pg.person_work_at", ["person", "company", "workFrom"]),
            ("person_hasInterest_tag", "pg.person_has_interest", ["person", "tag"]),
            ("forum_hasMember_person", "pg.forum_has_member", ["forum", "person", "joinDate"]),
            ("forum_hasTag_tag", "pg.forum_has_tag", ["forum", "tag"])):
        _, rows = read(source, name)
        ex.scope(relation, "relational", columns, [r[:len(columns)] for r in rows])
    ex.close()

    # --- oracle: LDBC's documented references, supertype targets as unions
    place_union, org_union = "ldbc.place-union.id", "ldbc.organisation-union.id"
    refs = [
        ("neo4j:-[IS_LOCATED_IN]->.source.id", org_union),
        ("neo4j:-[IS_LOCATED_IN]->.target.id", place_union),
        ("neo4j:-[IS_PART_OF]->.source.id", place_union),
        ("neo4j:-[IS_PART_OF]->.target.id", place_union),
        ("neo4j:-[HAS_TYPE]->.source.id", "neo4j:Tag.id"),
        ("neo4j:-[HAS_TYPE]->.target.id", "neo4j:TagClass.id"),
        ("neo4j:-[IS_SUBCLASS_OF]->.source.id", "neo4j:TagClass.id"),
        ("neo4j:-[IS_SUBCLASS_OF]->.target.id", "neo4j:TagClass.id"),
        ("social.post.creator", "pg.person.id"),
        ("social.post.place", "neo4j:Country.id"),
        ("social.post.forum", "pg.forum.id"),
        ("social.post@tags.tags", "neo4j:Tag.id"),
        ("social.comment.creator", "pg.person.id"),
        ("social.comment.place", "neo4j:Country.id"),
        ("social.comment.replyOfPost", "social.post.id"),
        ("social.comment.replyOfComment", "social.comment.id"),
        ("social.comment@tags.tags", "neo4j:Tag.id"),
        ("pg.person.place", "neo4j:City.id"),
        ("pg.forum.moderator", "pg.person.id"),
        ("pg.person_knows_person.person1", "pg.person.id"),
        ("pg.person_knows_person.person2", "pg.person.id"),
        ("pg.person_likes_post.person", "pg.person.id"),
        ("pg.person_likes_post.post", "social.post.id"),
        ("pg.person_likes_comment.person", "pg.person.id"),
        ("pg.person_likes_comment.comment", "social.comment.id"),
        ("pg.person_study_at.person", "pg.person.id"),
        ("pg.person_study_at.university", "neo4j:University.id"),
        ("pg.person_work_at.person", "pg.person.id"),
        ("pg.person_work_at.company", "neo4j:Company.id"),
        ("pg.person_has_interest.person", "pg.person.id"),
        ("pg.person_has_interest.tag", "neo4j:Tag.id"),
        ("pg.forum_has_member.forum", "pg.forum.id"),
        ("pg.forum_has_member.person", "pg.person.id"),
        ("pg.forum_has_tag.forum", "pg.forum.id"),
        ("pg.forum_has_tag.tag", "neo4j:Tag.id"),
    ]
    oracle = {
        "source": "LDBC SNB Interactive v1, SF0.1, CsvBasic (datasets.ldbcouncil.org)",
        "unions": [
            {"name": "ldbc.place-union", "column": "id",
             "members": ["neo4j:City", "neo4j:Continent", "neo4j:Country"], "demanded": True},
            {"name": "ldbc.organisation-union", "column": "id",
             "members": ["neo4j:Company", "neo4j:University"], "demanded": True},
            {"name": "ldbc.message-union", "column": "id",
             "members": ["social.comment", "social.post"], "demanded": False,
             "note": "an LDBC supertype, but no reference in this layout spans posts and comments"},
        ],
        "dependencies": [{"dependent": d, "referenced": r, "provenance": "publisher"} for d, r in refs],
    }
    (out / "ground_truth.json").write_text(json.dumps(oracle, indent=1), encoding="utf-8")
    print(f"{len(ex.catalog)} scopes, {sum(e['columns'] for e in ex.catalog)} extractors, "
          f"{sum(e['rows'] * e['columns'] for e in ex.catalog):,} cells -> {export_dir}")
    print(f"{len(refs)} oracle references, {sum(r.endswith('-union.id') for _, r in refs)} into unions")


if __name__ == "__main__":
    main()
