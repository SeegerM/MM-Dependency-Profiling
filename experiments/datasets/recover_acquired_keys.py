#!/usr/bin/env python3
"""Recover and validate keys and foreign-key-like relationships in acquired datasets.

This is deliberately source-aware. Declared relationships, exact inclusion dependencies, and
partial cross-source correspondences are reported separately instead of promoting every overlap to
a foreign key. The script reads the acquired files only and writes one JSON report.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import paths  # noqa: E402


NULLS = {"", "\\N", "N/A", "null", "None"}


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 8) if denominator else None


def coverage(total: int, matched: int, examples: list[str] | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "non_null_rows": total,
        "matched_rows": matched,
        "coverage": ratio(matched, total),
        "exact": total == matched,
    }
    if examples:
        result["missing_examples"] = examples[:10]
    return result


def add_missing(examples: list[str], value: object) -> None:
    text = str(value)
    if len(examples) < 10 and text not in examples:
        examples.append(text)


def csv_key(path: Path, columns: tuple[str, ...]) -> tuple[dict[str, object], set[object]]:
    seen: set[object] = set()
    rows = non_null = duplicates = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            rows += 1
            values = tuple(row[column].strip() for column in columns)
            if any(value in NULLS for value in values):
                continue
            non_null += 1
            key: object = values[0] if len(values) == 1 else values
            if key in seen:
                duplicates += 1
            else:
                seen.add(key)
    return {
        "columns": list(columns),
        "rows": rows,
        "non_null_rows": non_null,
        "distinct": len(seen),
        "duplicate_rows": duplicates,
        "null_rows": rows - non_null,
        "is_key": non_null == rows and duplicates == 0,
        "is_unique_when_present": duplicates == 0,
    }, seen


def resource_variants(name: str, include_film_suffixes: bool = True) -> list[str]:
    base = "_".join(name.strip().split())
    encoded = quote(base, safe="_,()-.!~*'")
    variants: list[str] = []
    for value in (base, encoded):
        candidates = (value, f"{value}_(film)", f"{value}_(movie)") if include_film_suffixes else (value,)
        for candidate in candidates:
            if candidate not in variants:
                variants.append(candidate)
    return variants


def graph_subjects(path: Path) -> tuple[set[str], int, int]:
    subjects: set[str] = set()
    rows = malformed = 0
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            parts = line.rstrip("\r\n").split("\t", 2)
            if len(parts) != 3:
                malformed += 1
                continue
            rows += 1
            subject = parts[0]
            if subject.startswith("<") and subject.endswith(">"):
                subject = subject[1:-1]
            subjects.add(subject)
    return subjects, rows, malformed


def graph_term(value: str) -> str:
    value = value.strip()
    if value.startswith("<") and value.endswith(">"):
        return value[1:-1]
    return value


def graph_profile(
    path: Path, collect_coordinates: bool = False
) -> tuple[dict[str, object], set[str], dict[str, dict[str, float]]]:
    subjects: set[str] = set()
    object_nodes: set[str] = set()
    predicates: Counter[str] = Counter()
    coordinates: dict[str, dict[str, float]] = {}
    rows = malformed = literal_objects = 0
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            parts = line.rstrip("\r\n").split("\t", 2)
            if len(parts) != 3:
                malformed += 1
                continue
            rows += 1
            subject = graph_term(parts[0])
            predicate = graph_term(parts[1])
            raw_object = parts[2].strip()
            subjects.add(subject)
            predicates[predicate] += 1
            if raw_object.startswith('"'):
                literal_objects += 1
            else:
                object_nodes.add(graph_term(raw_object))
            if collect_coordinates and predicate in {"latitude", "longitude"}:
                match = re.match(r'^"([^"\\]+)"', raw_object)
                if match:
                    try:
                        coordinates.setdefault(subject, {})[predicate] = float(match.group(1))
                    except ValueError:
                        pass
    return {
        "rows": rows,
        "malformed_rows": malformed,
        "distinct_subjects": len(subjects),
        "distinct_object_nodes": len(object_nodes),
        "distinct_nodes": len(subjects | object_nodes),
        "literal_object_rows": literal_objects,
        "distinct_predicates": len(predicates),
        "predicate_counts": dict(sorted(predicates.items())),
        "is_node_identifier_key": True,
    }, subjects, coordinates


def canonical_graph_fingerprints(path: Path) -> tuple[set[int], int]:
    """Return orientation-normalized in-process fingerprints for graph projection comparison."""
    fingerprints: set[int] = set()
    malformed = 0
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            parts = line.rstrip("\r\n").split("\t", 2)
            if len(parts) != 3:
                malformed += 1
                continue
            subject = graph_term(parts[0])
            raw_predicate = parts[1].strip()
            object_value = graph_term(parts[2])
            if raw_predicate.startswith("-"):
                predicate = graph_term(raw_predicate[1:])
                subject, object_value = object_value, subject
            else:
                predicate = graph_term(raw_predicate)
            fingerprints.add(hash((subject, predicate, object_value)))
    return fingerprints, malformed


def compare_graph_projections(directory: Path, namespace: str) -> dict[str, object]:
    actor, actor_malformed = canonical_graph_fingerprints(
        directory / f"actor.{namespace}.graph"
    )
    film, film_malformed = canonical_graph_fingerprints(directory / f"film.{namespace}.graph")
    mix, mix_malformed = canonical_graph_fingerprints(directory / f"mix.{namespace}.graph")
    union = actor | film
    return {
        "comparison": "orientation-normalized 64-bit in-process edge fingerprints",
        "actor_distinct_edges": len(actor),
        "film_distinct_edges": len(film),
        "actor_film_overlap": len(actor & film),
        "actor_film_union": len(union),
        "mix_distinct_edges": len(mix),
        "union_edges_missing_from_mix": len(union - mix),
        "extra_mix_edges": len(mix - union),
        "mix_equals_actor_film_union": mix == union,
        "malformed_rows": actor_malformed + film_malformed + mix_malformed,
    }


def name_coverage(
    values: Iterable[str], target_nodes: set[str], include_film_suffixes: bool = False
) -> dict[str, object]:
    occurrences = matched_occurrences = ambiguous_occurrences = 0
    distinct_values: set[str] = set()
    distinct_matches: set[str] = set()
    missing: list[str] = []
    for raw_value in values:
        value = raw_value.strip()
        if value in NULLS:
            continue
        occurrences += 1
        distinct_values.add(value)
        hits = {
            candidate
            for candidate in resource_variants(value, include_film_suffixes)
            if candidate in target_nodes
        }
        if hits:
            matched_occurrences += 1
            distinct_matches.add(value)
            ambiguous_occurrences += len(hits) > 1
        else:
            add_missing(missing, value)
    return {
        "non_null_occurrences": occurrences,
        "matched_occurrences": matched_occurrences,
        "occurrence_coverage": ratio(matched_occurrences, occurrences),
        "distinct_source_values": len(distinct_values),
        "matched_distinct_source_values": len(distinct_matches),
        "distinct_coverage": ratio(len(distinct_matches), len(distinct_values)),
        "ambiguous_occurrences": ambiguous_occurrences,
        "exact": occurrences == matched_occurrences,
        "missing_examples": missing,
        "classification": "normalized name-based entity correspondence, not a formal FK",
    }


def scalar_key_stats(values: Iterable[str], rows: int, columns: list[str]) -> dict[str, object]:
    seen: set[str] = set()
    non_null = duplicates = 0
    for raw_value in values:
        value = raw_value.strip()
        if value in NULLS:
            continue
        non_null += 1
        if value in seen:
            duplicates += 1
        else:
            seen.add(value)
    return {
        "columns": columns,
        "rows": rows,
        "non_null_rows": non_null,
        "null_rows": rows - non_null,
        "distinct": len(seen),
        "duplicate_rows": duplicates,
        "is_key": non_null == rows and duplicates == 0,
        "is_unique_when_present": duplicates == 0,
    }


def analyze_helsinki(root: Path) -> dict[str, object]:
    data = root / "upstream" / "helsinki-multimodel" / "data"
    patent_dir = data / "Patent_dataset" / "processed_dataset"

    assignee_key, assignees = csv_key(patent_dir / "assignee.table", ("ASSIGNEE",))
    class_key, classes = csv_key(patent_dir / "class.table", ("CLASS",))
    category_subcat_key, subcats = csv_key(patent_dir / "category.table", ("SUBCAT",))
    category_pair_key, category_pairs = csv_key(
        patent_dir / "category.table", ("CAT", "SUBCAT")
    )

    patent_ids: set[int] = set()
    patent_duplicates = 0
    patent_rows = 0
    fk_state: dict[str, list[object]] = {
        "patent.ASSIGNEE -> assignee.ASSIGNEE": [0, 0, []],
        "patent.NCLASS -> class.CLASS": [0, 0, []],
        "patent.(CAT,SUBCAT) -> category.(CAT,SUBCAT)": [0, 0, []],
    }
    with (patent_dir / "patent.table").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            patent_rows += 1
            patent = int(row["PATENT"])
            if patent in patent_ids:
                patent_duplicates += 1
            patent_ids.add(patent)
            checks: list[tuple[str, object, set[object]]] = [
                ("patent.ASSIGNEE -> assignee.ASSIGNEE", row["ASSIGNEE"].strip(), assignees),
                ("patent.NCLASS -> class.CLASS", row["NCLASS"].strip(), classes),
                (
                    "patent.(CAT,SUBCAT) -> category.(CAT,SUBCAT)",
                    (row["CAT"].strip(), row["SUBCAT"].strip()),
                    category_pairs,
                ),
            ]
            for name, value, target in checks:
                values = value if isinstance(value, tuple) else (value,)
                if any(item in NULLS for item in values):
                    continue
                fk_state[name][0] = int(fk_state[name][0]) + 1
                if value in target:
                    fk_state[name][1] = int(fk_state[name][1]) + 1
                else:
                    add_missing(fk_state[name][2], value)

    inventor_rows = inventor_fk_matches = inventor_duplicate_keys = 0
    inventor_monotonic = True
    inventor_missing: list[str] = []
    last_inventor: tuple[int, int] | None = None
    with (patent_dir / "inventor.table").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            inventor_rows += 1
            patent = int(row["PATENT"])
            key = (patent, int(row["INVSEQ"]))
            if last_inventor is not None:
                if key < last_inventor:
                    inventor_monotonic = False
                if key == last_inventor:
                    inventor_duplicate_keys += 1
            last_inventor = key
            if patent in patent_ids:
                inventor_fk_matches += 1
            else:
                add_missing(inventor_missing, patent)

    citation_rows = citing_matches = cited_matches = citation_duplicate_keys = 0
    citation_monotonic = True
    citing_missing: list[str] = []
    cited_missing: list[str] = []
    last_citation: tuple[int, int] | None = None
    with (patent_dir / "citation.graph").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            citation_rows += 1
            citing, cited = int(row["CITING"]), int(row["CITED"])
            key = (citing, cited)
            if last_citation is not None:
                if key < last_citation:
                    citation_monotonic = False
                if key == last_citation:
                    citation_duplicate_keys += 1
            last_citation = key
            if citing in patent_ids:
                citing_matches += 1
            else:
                add_missing(citing_missing, citing)
            if cited in patent_ids:
                cited_matches += 1
            else:
                add_missing(cited_missing, cited)

    class_pair_total = class_pair_matches = 0
    class_pair_missing: list[str] = []
    # CNAME is not quoted even when it contains commas.  CLASS is the first field and
    # SUBCAT/CAT are the final two fields, so recover the two identifiers from the right.
    with (patent_dir / "class.table").open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        next(reader)
        for row in reader:
            if len(row) < 4:
                continue
            key = (row[-1].strip(), row[-2].strip())
            if any(value in NULLS for value in key):
                continue
            class_pair_total += 1
            if key in category_pairs:
                class_pair_matches += 1
            else:
                add_missing(class_pair_missing, key)

    patent_relationships = {
        name: coverage(int(state[0]), int(state[1]), state[2])
        for name, state in fk_state.items()
    }
    patent_relationships.update(
        {
            "inventor.PATENT -> patent.PATENT": coverage(
                inventor_rows, inventor_fk_matches, inventor_missing
            ),
            "citation.CITING -> patent.PATENT": coverage(
                citation_rows, citing_matches, citing_missing
            ),
            "citation.CITED -> patent.PATENT": coverage(
                citation_rows, cited_matches, cited_missing
            ),
            "class.(CAT,SUBCAT) -> category.(CAT,SUBCAT)": coverage(
                class_pair_total, class_pair_matches, class_pair_missing
            ),
        }
    )

    university_dir = data / "University_dataset" / "processed_dataset"
    university_path = university_dir / "university_scorecard.table"
    unit_key, _ = csv_key(university_path, ("UNITID",))
    opeid_key, _ = csv_key(university_path, ("OPEID",))
    opeid6_key, _ = csv_key(university_path, ("opeid6",))
    institution_key, _ = csv_key(university_path, ("INSTNM",))
    university_url_key, _ = csv_key(university_path, ("INSTURL",))
    university_graph, university_nodes, graph_coordinates = graph_profile(
        university_dir / "location.graph", collect_coordinates=True
    )
    graph_coordinate_counts: Counter[tuple[float, float]] = Counter()
    for coordinate in graph_coordinates.values():
        if "latitude" in coordinate and "longitude" in coordinate:
            graph_coordinate_counts[
                (round(coordinate["latitude"], 4), round(coordinate["longitude"], 4))
            ] += 1

    university_rows = university_width_errors = 0
    university_names: list[str] = []
    university_coordinates: set[tuple[float, float]] = set()
    university_coordinate_rows = university_coordinate_duplicates = 0
    university_coordinate_matches = university_coordinate_unique_matches = 0
    university_coordinate_ambiguous_matches = 0
    with university_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        university_column_count = len(reader.fieldnames or [])
        for row in reader:
            university_rows += 1
            university_width_errors += None in row
            university_names.append(row["INSTNM"])
            latitude, longitude = row["LATITUDE"].strip(), row["LONGITUDE"].strip()
            if latitude in NULLS or longitude in NULLS:
                continue
            pair = (round(float(latitude), 4), round(float(longitude), 4))
            university_coordinate_rows += 1
            if pair in university_coordinates:
                university_coordinate_duplicates += 1
            university_coordinates.add(pair)
            target_count = graph_coordinate_counts[pair]
            if target_count:
                university_coordinate_matches += 1
            if target_count == 1:
                university_coordinate_unique_matches += 1
            elif target_count > 1:
                university_coordinate_ambiguous_matches += 1

    film_dir = data / "Film_dataset" / "processed_dataset"
    film_graph_profiles: dict[str, object] = {}
    film_graph_subjects: dict[str, set[str]] = {}
    for filename in (
        "mix.dbpedia.graph",
        "mix.yago.graph",
        "actor.dbpedia.graph",
        "actor.yago.graph",
        "film.dbpedia.graph",
        "film.yago.graph",
    ):
        profile, subjects, _ = graph_profile(film_dir / filename)
        film_graph_profiles[filename] = profile
        if filename.startswith(("actor.", "film.")):
            film_graph_subjects[filename] = subjects
    film_graph_projection_consistency = {
        namespace: compare_graph_projections(film_dir, namespace)
        for namespace in ("dbpedia", "yago")
    }

    film_rows = film_valid = film_invalid = film_blank = 0
    film_success = film_error_responses = 0
    imdb_ids: set[str] = set()
    imdb_non_null_rows = imdb_duplicates = imdb_exact_duplicate_rows = 0
    imdb_conflicting_rows = 0
    imdb_conflicting_ids: set[str] = set()
    imdb_first_document: dict[str, str] = {}
    document_signatures: set[str] = set()
    exact_duplicate_documents = 0
    title_years: set[tuple[str, str]] = set()
    title_year_duplicates = 0
    film_titles: list[str] = []
    film_people: dict[str, list[str]] = {"Actors": [], "Director": [], "Writer": []}
    film_field_presence: Counter[str] = Counter()
    with (film_dir / "film.imdb.json").open(encoding="utf-8") as stream:
        for line in stream:
            film_rows += 1
            if not line.strip():
                film_blank += 1
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                film_invalid += 1
                continue
            film_valid += 1
            film_field_presence.update(item.keys())
            signature = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            if signature in document_signatures:
                exact_duplicate_documents += 1
            else:
                document_signatures.add(signature)
            if str(item.get("Response", "")).lower() == "true":
                film_success += 1
            else:
                film_error_responses += 1
            imdb = str(item.get("imdbID", "")).strip()
            if imdb not in NULLS:
                imdb_non_null_rows += 1
                if imdb in imdb_ids:
                    imdb_duplicates += 1
                    if imdb_first_document[imdb] == signature:
                        imdb_exact_duplicate_rows += 1
                    else:
                        imdb_conflicting_rows += 1
                        imdb_conflicting_ids.add(imdb)
                else:
                    imdb_ids.add(imdb)
                    imdb_first_document[imdb] = signature
            title = str(item.get("Title", "")).strip()
            year = str(item.get("Year", "")).strip()
            if title not in NULLS:
                key = (title, year)
                if key in title_years:
                    title_year_duplicates += 1
                title_years.add(key)
                film_titles.append(title)
            for field in film_people:
                value = str(item.get(field, "")).strip()
                if value in NULLS:
                    continue
                for part in value.split(","):
                    person_name = re.sub(r"\s*\([^)]*\)\s*$", "", part).strip()
                    if person_name not in NULLS:
                        film_people[field].append(person_name)

    film_relationships = {
        "JSON Title -> film.dbpedia subject": name_coverage(
            film_titles, film_graph_subjects["film.dbpedia.graph"], True
        ),
        "JSON Title -> film.yago subject": name_coverage(
            film_titles, film_graph_subjects["film.yago.graph"], True
        ),
    }
    for field, values in film_people.items():
        film_relationships[f"JSON {field}[*] -> actor.dbpedia subject"] = name_coverage(
            values, film_graph_subjects["actor.dbpedia.graph"]
        )
        film_relationships[f"JSON {field}[*] -> actor.yago subject"] = name_coverage(
            values, film_graph_subjects["actor.yago.graph"]
        )

    person_dir = data / "Person_dataset" / "processed_dataset"
    person_graph, person_subjects, _ = graph_profile(person_dir / "person.graph")
    person_rows = person_valid = person_invalid = person_page_count = 0
    person_page_duplicates = person_page_map_key_mismatches = person_multi_page_documents = 0
    person_page_ids: set[int] = set()
    person_source_names: list[str] = []
    person_normalized_names: list[str] = []
    person_page_titles: list[str] = []
    with (person_dir / "person.json").open(encoding="utf-8") as stream:
        for line in stream:
            person_rows += 1
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                person_invalid += 1
                continue
            person_valid += 1
            normalized = item.get("query", {}).get("normalized", [])
            for value in normalized:
                if value.get("from") is not None:
                    person_source_names.append(str(value["from"]))
                if value.get("to") is not None:
                    person_normalized_names.append(str(value["to"]))
            pages = item.get("query", {}).get("pages", {})
            person_multi_page_documents += len(pages) != 1
            for page_map_key, page in pages.items():
                person_page_count += 1
                pageid = page.get("pageid")
                if isinstance(pageid, int):
                    if pageid in person_page_ids:
                        person_page_duplicates += 1
                    person_page_ids.add(pageid)
                    person_page_map_key_mismatches += str(pageid) != str(page_map_key)
                if page.get("title") is not None:
                    person_page_titles.append(str(page["title"]))

    return {
        "patent": {
            "keys": {
                "patent.PATENT": {
                    "rows": patent_rows,
                    "distinct": len(patent_ids),
                    "duplicate_rows": patent_duplicates,
                    "is_key": patent_duplicates == 0 and len(patent_ids) == patent_rows,
                },
                "assignee.ASSIGNEE": assignee_key,
                "class.CLASS": class_key,
                "category.SUBCAT": category_subcat_key,
                "category.(CAT,SUBCAT)": category_pair_key,
                "inventor.(PATENT,INVSEQ)": {
                    "rows": inventor_rows,
                    "monotonic": inventor_monotonic,
                    "duplicate_rows": inventor_duplicate_keys,
                    "is_key": inventor_monotonic and inventor_duplicate_keys == 0,
                },
                "citation.(CITING,CITED)": {
                    "rows": citation_rows,
                    "monotonic": citation_monotonic,
                    "duplicate_rows": citation_duplicate_keys,
                    "is_key": citation_monotonic and citation_duplicate_keys == 0,
                },
            },
            "relationships": patent_relationships,
        },
        "university": {
            "source_schema": {
                "file": "processed_dataset/university_scorecard.yaml",
                "declared_unique_api_field": "id",
                "source_column": "UNITID",
            },
            "table_shape": {
                "rows": university_rows,
                "columns": university_column_count,
                "row_width_errors": university_width_errors,
            },
            "keys": {
                "university.UNITID": unit_key,
                "university.OPEID": opeid_key,
                "university.opeid6": opeid6_key,
                "university.INSTNM": institution_key,
                "university.INSTURL": university_url_key,
                "university.(LATITUDE,LONGITUDE)": {
                    "rows": university_rows,
                    "non_null_rows": university_coordinate_rows,
                    "distinct": len(university_coordinates),
                    "duplicate_rows": university_coordinate_duplicates,
                    "null_rows": university_rows - university_coordinate_rows,
                    "is_key": university_coordinate_rows == university_rows
                    and university_coordinate_duplicates == 0,
                },
                "location.(latitude,longitude)": {
                    "coordinate_nodes": sum(graph_coordinate_counts.values()),
                    "distinct_coordinate_pairs": len(graph_coordinate_counts),
                    "ambiguous_coordinate_pairs": sum(
                        count > 1 for count in graph_coordinate_counts.values()
                    ),
                    "is_key": all(count == 1 for count in graph_coordinate_counts.values()),
                },
            },
            "graphs": {"location.graph": university_graph},
            "relationships": {
                "university.INSTNM -> location subject": name_coverage(
                    university_names, university_nodes
                ),
                "university.(LATITUDE,LONGITUDE) -> location.(latitude,longitude) rounded to 4 decimals": {
                    **coverage(
                        university_coordinate_rows,
                        university_coordinate_matches,
                        [str(value) for value in sorted(
                            university_coordinates - set(graph_coordinate_counts)
                        )[:10]],
                    ),
                    "unique_target_matches": university_coordinate_unique_matches,
                    "ambiguous_target_matches": university_coordinate_ambiguous_matches,
                    "classification": "coordinate-based cross-model correspondence; an FK only if the target coordinate is unique",
                },
            },
        },
        "film": {
            "document_shape": {
                "physical_lines": film_rows,
                "valid_documents": film_valid,
                "invalid_lines": film_invalid,
                "blank_lines": film_blank,
                "successful_responses": film_success,
                "error_responses": film_error_responses,
                "exact_duplicate_documents": exact_duplicate_documents,
                "field_presence": dict(sorted(film_field_presence.items())),
            },
            "keys": {
                "JSON imdbID": {
                    "rows": film_valid,
                    "non_null_rows": imdb_non_null_rows,
                    "null_rows": film_valid - imdb_non_null_rows,
                    "distinct": len(imdb_ids),
                    "duplicate_rows": imdb_duplicates,
                    "exact_duplicate_rows_for_same_id": imdb_exact_duplicate_rows,
                    "conflicting_duplicate_rows_for_same_id": imdb_conflicting_rows,
                    "conflicting_ids": len(imdb_conflicting_ids),
                    "conflicting_id_examples": sorted(imdb_conflicting_ids)[:10],
                    "is_key": imdb_non_null_rows == film_valid and imdb_duplicates == 0,
                    "is_unique_after_exact_document_deduplication": imdb_conflicting_rows == 0,
                },
                "JSON (Title,Year)": {
                    "rows": film_valid,
                    "non_null_rows": len(film_titles),
                    "distinct": len(title_years),
                    "duplicate_rows": title_year_duplicates,
                    "is_unique_when_present": title_year_duplicates == 0,
                },
            },
            "graphs": film_graph_profiles,
            "graph_projection_consistency": film_graph_projection_consistency,
            "relationships": film_relationships,
        },
        "person": {
            "document_shape": {
                "physical_lines": person_rows,
                "valid_documents": person_valid,
                "invalid_lines": person_invalid,
                "pages": person_page_count,
                "documents_not_having_exactly_one_page": person_multi_page_documents,
                "page_map_key_mismatches": person_page_map_key_mismatches,
            },
            "keys": {
                "JSON pageid": {
                    "documents": person_valid,
                    "pages": person_page_count,
                    "distinct": len(person_page_ids),
                    "duplicate_rows": person_page_duplicates,
                    "is_key": person_page_duplicates == 0 and person_page_count == len(person_page_ids),
                },
                "JSON query.normalized.from": scalar_key_stats(
                    person_source_names, person_valid, ["query.normalized.from"]
                ),
                "JSON query.normalized.to": scalar_key_stats(
                    person_normalized_names, person_valid, ["query.normalized.to"]
                ),
                "JSON query.pages.*.title": scalar_key_stats(
                    person_page_titles, person_page_count, ["query.pages.*.title"]
                ),
            },
            "graphs": {"person.graph": person_graph},
            "relationships": {
                "JSON query.normalized.from -> person graph subject": name_coverage(
                    person_source_names, person_subjects
                ),
                "JSON query.normalized.to -> person graph subject": name_coverage(
                    person_normalized_names, person_subjects
                ),
                "JSON page title -> person graph subject": name_coverage(
                    person_page_titles, person_subjects
                ),
            },
        },
    }


def analyze_movies(root: Path) -> dict[str, object]:
    data = root / "upstream" / "movielens-tmdb-wikidata" / "data"
    ml = data / "ml-32m"
    movie_key, movie_ids_raw = csv_key(ml / "movies.csv", ("movieId",))
    movie_ids = {int(value) for value in movie_ids_raw}
    links_movie_key, _ = csv_key(ml / "links.csv", ("movieId",))
    links_imdb_key, links_imdb = csv_key(ml / "links.csv", ("imdbId",))
    links_tmdb_key, links_tmdb_raw = csv_key(ml / "links.csv", ("tmdbId",))
    links_tmdb = {int(value) for value in links_tmdb_raw}

    relationship_counts: dict[str, list[object]] = {
        "ratings.movieId -> movies.movieId": [0, 0, []],
        "tags.movieId -> movies.movieId": [0, 0, []],
        "links.movieId -> movies.movieId": [0, 0, []],
    }

    ratings_rows = ratings_duplicate_pairs = 0
    ratings_monotonic = True
    last_rating_pair: tuple[int, int] | None = None
    with (ml / "ratings.csv").open("rb") as stream:
        next(stream)
        for line in stream:
            ratings_rows += 1
            user, movie, _, _ = line.rstrip(b"\r\n").split(b",", 3)
            pair = (int(user), int(movie))
            if last_rating_pair is not None:
                ratings_monotonic &= pair >= last_rating_pair
                ratings_duplicate_pairs += pair == last_rating_pair
            last_rating_pair = pair
            state = relationship_counts["ratings.movieId -> movies.movieId"]
            state[0] = int(state[0]) + 1
            if pair[1] in movie_ids:
                state[1] = int(state[1]) + 1
            else:
                add_missing(state[2], pair[1])

    tags_rows = tags_exact_duplicate_rows = 0
    seen_tag_lines: set[bytes] = set()
    with (ml / "tags.csv").open("rb") as stream:
        next(stream)
        for line in stream:
            tags_rows += 1
            normalized = line.rstrip(b"\r\n")
            if normalized in seen_tag_lines:
                tags_exact_duplicate_rows += 1
            else:
                seen_tag_lines.add(normalized)
            _, movie, _ = normalized.split(b",", 2)
            movie_id = int(movie)
            state = relationship_counts["tags.movieId -> movies.movieId"]
            state[0] = int(state[0]) + 1
            if movie_id in movie_ids:
                state[1] = int(state[1]) + 1
            else:
                add_missing(state[2], movie_id)
    del seen_tag_lines

    with (ml / "links.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            movie_id = int(row["movieId"])
            state = relationship_counts["links.movieId -> movies.movieId"]
            state[0] = int(state[0]) + 1
            if movie_id in movie_ids:
                state[1] = int(state[1]) + 1
            else:
                add_missing(state[2], movie_id)

    tmdb_ids: set[int] = set()
    tmdb_rows = tmdb_duplicates = tmdb_invalid = 0
    tmdb_path = data / "tmdb" / "movie_ids_08_29_2026.jsonl"
    with tmdb_path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                tmdb_invalid += 1
                continue
            tmdb_rows += 1
            identifier = int(item["id"])
            if identifier in tmdb_ids:
                tmdb_duplicates += 1
            tmdb_ids.add(identifier)

    tmdb_missing = sorted(links_tmdb - tmdb_ids)
    tmdb_link_coverage = coverage(len(links_tmdb), len(links_tmdb) - len(tmdb_missing), tmdb_missing)

    wikidata_path = data / "wikidata" / "movie-links.jsonl"
    wikidata_rows = wikidata_invalid = wikidata_duplicate_tuples = 0
    wikidata_duplicate_pairs = wikidata_tuple_null_rows = wikidata_pair_null_rows = 0
    wikidata_tuples: set[tuple[object, ...]] = set()
    wikidata_pairs: set[tuple[object, ...]] = set()
    wikidata_qids: set[str] = set()
    wikidata_imdb: set[str] = set()
    wikidata_tmdb: set[int] = set()
    wikidata_movie_ids: set[int] = set()
    with wikidata_path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                wikidata_invalid += 1
                continue
            wikidata_rows += 1
            qid = item.get("wikidata_id")
            imdb = item.get("imdb_id")
            tmdb = item.get("wikidata_tmdb_id")
            key = (qid, imdb, tmdb)
            if key in wikidata_tuples:
                wikidata_duplicate_tuples += 1
            wikidata_tuples.add(key)
            pair = (qid, imdb)
            if pair in wikidata_pairs:
                wikidata_duplicate_pairs += 1
            wikidata_pairs.add(pair)
            wikidata_tuple_null_rows += any(value is None or value == "" for value in key)
            wikidata_pair_null_rows += any(value is None or value == "" for value in pair)
            if isinstance(qid, str) and qid:
                wikidata_qids.add(qid)
            if isinstance(imdb, str) and imdb:
                wikidata_imdb.add(imdb)
            if isinstance(tmdb, int):
                wikidata_tmdb.add(tmdb)
            wikidata_movie_ids.update(int(value) for value in item["movielens_movie_ids"])

    normalized_links_imdb = {f"tt{int(value):07d}" for value in links_imdb}
    movie_relationships = {
        name: coverage(int(state[0]), int(state[1]), state[2])
        for name, state in relationship_counts.items()
    }
    movie_relationships.update(
        {
            "links.tmdbId -> TMDB.id": tmdb_link_coverage,
            "Wikidata.imdb_id -> normalized links.imdbId": coverage(
                len(wikidata_imdb), len(wikidata_imdb & normalized_links_imdb)
            ),
            "normalized links.imdbId -> Wikidata.imdb_id": {
                **coverage(len(normalized_links_imdb), len(normalized_links_imdb & wikidata_imdb)),
                "classification": "partial source-bounded cross-source relationship",
            },
            "Wikidata.movielens_movie_ids[*] -> movies.movieId": coverage(
                len(wikidata_movie_ids), len(wikidata_movie_ids & movie_ids)
            ),
            "Wikidata.wikidata_tmdb_id -> TMDB.id": coverage(
                len(wikidata_tmdb), len(wikidata_tmdb & tmdb_ids)
            ),
        }
    )

    return {
        "keys": {
            "movies.movieId": movie_key,
            "links.movieId": links_movie_key,
            "links.imdbId": links_imdb_key,
            "links.tmdbId": links_tmdb_key,
            "ratings.(userId,movieId)": {
                "rows": ratings_rows,
                "monotonic": ratings_monotonic,
                "duplicate_rows": ratings_duplicate_pairs,
                "is_key": ratings_monotonic and ratings_duplicate_pairs == 0,
            },
            "tags.(userId,movieId,tag,timestamp)": {
                "rows": tags_rows,
                "exact_duplicate_rows": tags_exact_duplicate_rows,
                "is_key": tags_exact_duplicate_rows == 0,
            },
            "TMDB.id": {
                "rows": tmdb_rows,
                "invalid_rows": tmdb_invalid,
                "distinct": len(tmdb_ids),
                "duplicate_rows": tmdb_duplicates,
                "is_key": tmdb_invalid == 0 and tmdb_duplicates == 0,
            },
            "Wikidata.(wikidata_id,imdb_id,wikidata_tmdb_id)": {
                "rows": wikidata_rows,
                "invalid_rows": wikidata_invalid,
                "null_rows": wikidata_tuple_null_rows,
                "distinct": len(wikidata_tuples),
                "duplicate_rows": wikidata_duplicate_tuples,
                "is_key": wikidata_invalid == 0
                and wikidata_tuple_null_rows == 0
                and wikidata_duplicate_tuples == 0,
                "is_unique_document_tuple": wikidata_duplicate_tuples == 0,
            },
            "Wikidata.(wikidata_id,imdb_id)": {
                "rows": wikidata_rows,
                "invalid_rows": wikidata_invalid,
                "null_rows": wikidata_pair_null_rows,
                "distinct": len(wikidata_pairs),
                "duplicate_rows": wikidata_duplicate_pairs,
                "is_key": wikidata_invalid == 0
                and wikidata_pair_null_rows == 0
                and wikidata_duplicate_pairs == 0,
            },
            "Wikidata.wikidata_id": {
                "rows": wikidata_rows,
                "distinct": len(wikidata_qids),
                "is_key": len(wikidata_qids) == wikidata_rows,
            },
            "Wikidata.imdb_id": {
                "rows": wikidata_rows,
                "distinct": len(wikidata_imdb),
                "is_key": len(wikidata_imdb) == wikidata_rows,
            },
        },
        "relationships": movie_relationships,
    }


def parse_column(reference: str) -> tuple[str, int]:
    match = re.fullmatch(r"pg\.([^.]+)\.column(\d+)", reference)
    if not match:
        raise ValueError(f"unsupported referenced endpoint: {reference}")
    return match.group(1), int(match.group(2)) - 1


def validate_musicbrainz_declared_keys(
    data_root: Path, dependencies: list[dict[str, object]]
) -> list[dict[str, object]]:
    references = Counter(str(item["referenced"]) for item in dependencies)
    results: list[dict[str, object]] = []
    for reference, inbound_count in sorted(references.items()):
        table, index = parse_column(reference)
        path = data_root / "Musicbrainz" / table
        seen: set[str] = set()
        rows = non_null = duplicates = short_rows = 0
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                rows += 1
                fields = line.rstrip("\r\n").split("\t")
                if index >= len(fields):
                    short_rows += 1
                    continue
                value = fields[index]
                if value in NULLS:
                    continue
                non_null += 1
                if value in seen:
                    duplicates += 1
                else:
                    seen.add(value)
        results.append(
            {
                "endpoint": reference,
                "inbound_declared_foreign_keys": inbound_count,
                "rows": rows,
                "non_null_rows": non_null,
                "distinct": len(seen),
                "duplicate_rows": duplicates,
                "short_rows": short_rows,
                "is_unique_when_present": duplicates == 0,
                "is_non_null_key": duplicates == 0 and non_null == rows,
            }
        )
    return results


def scan_gid_matches(path: Path, wanted: set[str]) -> dict[str, object]:
    pending = set(wanted)
    rows = malformed = 0
    with path.open("rb") as stream:
        for line in stream:
            rows += 1
            fields = line.rstrip(b"\r\n").split(b"\t", 2)
            if len(fields) < 2:
                malformed += 1
                continue
            if pending:
                pending.discard(fields[1].decode("ascii", errors="ignore"))
    return {
        "target_rows_scanned": rows,
        "target_malformed_rows": malformed,
        "distinct_source_values": len(wanted),
        "matched_source_values": len(wanted) - len(pending),
        "coverage": ratio(len(wanted) - len(pending), len(wanted)),
        "exact": not pending,
        "missing_examples": sorted(pending)[:10],
    }


def jsonl_key(path: Path, key_name: str) -> dict[str, object]:
    rows = invalid = nulls = duplicates = 0
    monotonic = True
    last: str | None = None
    seen: set[str] = set()
    wikidata_links = 0
    wikidata_ids: set[str] = set()
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            rows += 1
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            value = item.get(key_name)
            if not isinstance(value, str) or not value:
                nulls += 1
                continue
            if last is not None:
                monotonic &= value >= last
            last = value
            if value in seen:
                duplicates += 1
            else:
                seen.add(value)
            if key_name == "artist_mbid":
                link = item.get("artist_data", {}).get("rels", {}).get("wikidata")
                if isinstance(link, str) and link:
                    wikidata_links += 1
                    wikidata_ids.add(link.rstrip("/").rsplit("/", 1)[-1])
    result: dict[str, object] = {
        "rows": rows,
        "invalid_rows": invalid,
        "null_rows": nulls,
        "distinct": len(seen),
        "monotonic": monotonic,
        "duplicate_rows": duplicates,
        "is_key": invalid == 0 and nulls == 0 and duplicates == 0,
    }
    if key_name == "artist_mbid":
        result["explicit_wikidata_links"] = wikidata_links
        result["distinct_wikidata_ids"] = len(wikidata_ids)
    return result


def popularity_keys(path: Path) -> tuple[dict[str, object], set[str], set[str]]:
    rows = 0
    artists: set[str] = set()
    recordings: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    artist_nulls = recording_nulls = pair_nulls = 0
    artist_duplicates = recording_duplicates = pair_duplicates = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            rows += 1
            artist = row["artist_mbid"].strip()
            recording = row["recording_mbid"].strip()
            if artist in NULLS:
                artist_nulls += 1
            elif artist in artists:
                artist_duplicates += 1
            else:
                artists.add(artist)
            if recording in NULLS:
                recording_nulls += 1
            elif recording in recordings:
                recording_duplicates += 1
            else:
                recordings.add(recording)
            if artist in NULLS or recording in NULLS:
                pair_nulls += 1
            elif (artist, recording) in pairs:
                pair_duplicates += 1
            else:
                pairs.add((artist, recording))

    def key(columns: list[str], nulls: int, distinct: int, duplicates: int) -> dict[str, object]:
        return {
            "columns": columns,
            "rows": rows,
            "non_null_rows": rows - nulls,
            "distinct": distinct,
            "duplicate_rows": duplicates,
            "null_rows": nulls,
            "is_key": nulls == 0 and duplicates == 0,
            "is_unique_when_present": duplicates == 0,
        }

    return {
        "artist_mbid": key(["artist_mbid"], artist_nulls, len(artists), artist_duplicates),
        "recording_mbid": key(
            ["recording_mbid"], recording_nulls, len(recordings), recording_duplicates
        ),
        "(artist_mbid,recording_mbid)": key(
            ["artist_mbid", "recording_mbid"], pair_nulls, len(pairs), pair_duplicates
        ),
    }, artists, recordings


def distinct_coverage(source: set[str], target: set[str]) -> dict[str, object]:
    missing = sorted(source - target)
    return {
        **coverage(len(source), len(source) - len(missing), missing),
        "unit": "distinct source values",
    }


def analyze_music(root: Path, data_root: Path) -> dict[str, object]:
    bundle = root / "upstream" / "musicbrainz-listenbrainz-wikidata" / "data"
    spec = json.loads(
        (root / "experiments" / "datasets" / "specs" / "musicbrainz-full.json").read_text()
    )
    dependencies: list[dict[str, object]] = spec["dependencies"]
    declared_key_stats = validate_musicbrainz_declared_keys(data_root, dependencies)

    listen_file = next((bundle / "listenbrainz-incremental-20260828").rglob("*.listens"))
    mbids = {
        "artist": set(),
        "recording": set(),
        "release": set(),
        "release_group": set(),
    }
    listen_rows = invalid_listens = rows_with_user_and_timestamp = 0
    with listen_file.open(encoding="utf-8") as stream:
        for line in stream:
            listen_rows += 1
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                invalid_listens += 1
                continue
            rows_with_user_and_timestamp += item.get("user_id") is not None and item.get("timestamp") is not None
            info = item.get("track_metadata", {}).get("additional_info", {})
            for value in info.get("artist_mbids") or []:
                if isinstance(value, str) and value:
                    mbids["artist"].add(value)
            for name in ("recording", "release", "release_group"):
                value = info.get(f"{name}_mbid")
                if isinstance(value, str) and value:
                    mbids[name].add(value)

    event_relationships = {
        f"listens.{name}_mbid -> MusicBrainz.{name}.gid": scan_gid_matches(
            data_root / "Musicbrainz" / name, values
        )
        for name, values in mbids.items()
    }
    sample = bundle / "listenbrainz-sample"
    cache_files = {
        "artists_cache.artist_mbid": (next(sample.rglob("artists_cache.jsonl")), "artist_mbid"),
        "recordings_cache.recording_mbid": (
            next(sample.rglob("recordings_cache.jsonl")),
            "recording_mbid",
        ),
        "release_groups_cache.release_group_mbid": (
            next(sample.rglob("release_groups_cache.jsonl")),
            "release_group_mbid",
        ),
    }
    cache_keys = {name: jsonl_key(path, field) for name, (path, field) in cache_files.items()}
    artist_cache_ids: set[str] = set()
    with cache_files["artists_cache.artist_mbid"][0].open(encoding="utf-8") as stream:
        for line in stream:
            try:
                value = json.loads(line).get("artist_mbid")
            except json.JSONDecodeError:
                continue
            if isinstance(value, str) and value:
                artist_cache_ids.add(value)

    popularity_dir = next(sample.rglob("lbdump/popularity"))
    top_keys, top_artists, top_recordings = popularity_keys(
        popularity_dir / "top_recording.csv"
    )
    mlhd_keys, mlhd_artists, mlhd_recordings = popularity_keys(
        popularity_dir / "mlhd_top_recording.csv"
    )
    event_relationships.update(
        {
            "listens.recording_mbid -> top_recording.recording_mbid": distinct_coverage(
                mbids["recording"], top_recordings
            ),
            "listens.artist_mbids[*] -> top_recording.artist_mbid": distinct_coverage(
                mbids["artist"], top_artists
            ),
            "top_recording.artist_mbid -> artists_cache.artist_mbid": distinct_coverage(
                top_artists, artist_cache_ids
            ),
            "mlhd_top_recording.artist_mbid -> artists_cache.artist_mbid": distinct_coverage(
                mlhd_artists, artist_cache_ids
            ),
        }
    )

    by_models = Counter(str(item["models"]) for item in dependencies)
    unique_declared_keys = sum(item["is_unique_when_present"] for item in declared_key_stats)
    non_null_declared_keys = sum(item["is_non_null_key"] for item in declared_key_stats)
    return {
        "musicbrainz_declared_schema": {
            "foreign_keys": len(dependencies),
            "cross_model_after_existing_conversion": sum(
                bool(item["cross_model"]) for item in dependencies
            ),
            "by_models": dict(sorted(by_models.items())),
            "referenced_key_endpoints": len(declared_key_stats),
            "unique_when_present_endpoints": unique_declared_keys,
            "non_null_key_endpoints": non_null_declared_keys,
            "referenced_key_validation": declared_key_stats,
            "source": "musicBrainzMock/Metadata/ForeignKeys.csv via musicbrainz-full.json",
        },
        "listenbrainz_event_keys": {
            "rows": listen_rows,
            "invalid_rows": invalid_listens,
            "rows_with_user_id_and_timestamp": rows_with_user_and_timestamp,
            "primary_key": None,
            "note": "The dump exposes no immutable listen identifier; recording_msid identifies a recording submission, not a listen event.",
        },
        "listenbrainz_cache_keys": cache_keys,
        "listenbrainz_popularity_keys": {
            "top_recording": top_keys,
            "mlhd_top_recording": mlhd_keys,
        },
        "relationships": event_relationships,
        "wikidata_relationship": {
            "source": "artists_cache.artist_data.rels.wikidata",
            "classification": "explicit source-provided cross-source relationship",
            "links": cache_keys["artists_cache.artist_mbid"].get("explicit_wikidata_links", 0),
            "distinct_wikidata_ids": cache_keys["artists_cache.artist_mbid"].get(
                "distinct_wikidata_ids", 0
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--data-root", type=Path, default=paths.DATA_ROOT)
    parser.add_argument(
        "--output", type=Path, default=default_root / "results" / "acquired-keys.json"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    output = args.output.resolve()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology": {
            "key": "non-null uniqueness in the stored snapshot, or graph node identity",
            "foreign_key": "all non-null dependent values occur in a unique referenced domain",
            "partial": "reported as correspondence/partial IND, never promoted to a formal FK",
        },
    }
    print("Scanning Helsinki...", flush=True)
    report["helsinki"] = analyze_helsinki(root)
    print("Scanning MovieLens/TMDB/Wikidata...", flush=True)
    report["movielens_tmdb_wikidata"] = analyze_movies(root)
    print("Scanning MusicBrainz/ListenBrainz/Wikidata...", flush=True)
    report["musicbrainz_listenbrainz_wikidata"] = analyze_music(
        root, args.data_root.resolve()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".partial")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(f"Wrote {output}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
