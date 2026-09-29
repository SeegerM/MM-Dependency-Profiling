"""Classifies each discovered cross-store link by how its two extractor names differ.

Four classes, tested in order, so that a spelling variant is not reported as a synonym:

  identical         the normalised names agree
  string-variant    a typo, prefix convention, tokenisation difference, or containment
  lexical-semantic  related concepts by Wu-Palmer similarity over WordNet (max across senses)
  unrelated         none of the above

"string-variant" rather than "syntactic", which refers to edit distance between values.

usage: name_similarity.py <exportDir> <indResultDir> [--string=0.7] [--lexical=0.7]
"""
import json
import re
import sys
from pathlib import Path

POSITIONAL = [a for a in sys.argv[1:] if not a.startswith("--")]
# Optional, so the module can also be imported as a library.
EXPORT = Path(POSITIONAL[0]) if POSITIONAL else None
RESULTS = Path(POSITIONAL[1]) if len(POSITIONAL) > 1 else None


def flag(name, default):
    return float(next((a.split("=", 1)[1] for a in sys.argv[1:]
                       if a.startswith(f"--{name}=")), default))


STRING_VARIANT = flag("string", 0.7)
LEXICAL = flag("lexical", 0.7)
TRAILING_COMMA = re.compile(r",\s*([}\]])")
CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

# Common identifier tokens that carry no concept.
STOPWORDS = {"id", "key", "no", "num", "code", "ref", "fk", "pk", ""}


def store_of(relation, stores):
    if relation in stores:
        return stores[relation]
    base = relation.split("@")[0]
    if base in stores:
        return stores[base]
    if base.startswith("neo4j:"):
        return "graph"
    if base.startswith(("unibench.", "shop.", "mongo.")):
        return "document"
    return "relational"


def tokens(attribute):
    """Content words of an attribute name, in order.

    The leaf of a dotted path is taken, single-letter prefixes of the TPC-H kind are dropped, and
    camel case is split, so that c_nationkey and n_nationkey reduce to the same token and
    knownForTitles to three.
    """
    leaf = attribute.split(".")[-1]
    parts = CAMEL.sub(" ", leaf).replace("_", " ").replace("-", " ").lower().split()
    parts = [p for p in parts if not (len(p) == 1 and p.isalpha())]
    return parts


def normalised(attribute):
    return " ".join(tokens(attribute))


def levenshtein_ratio(a, b):
    if not a or not b:
        return 0.0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return 1.0 - previous[-1] / max(len(a), len(b))


def syntactic_score(a, b):
    """String relatedness of two normalised names, including containment."""
    left, right = set(a.split()), set(b.split())
    jaccard = len(left & right) / len(left | right) if left | right else 0.0
    contained = 1.0 if (left <= right or right <= left) and left and right else 0.0
    return max(levenshtein_ratio(a, b), jaccard, contained)


class WordNet:
    """Wu-Palmer similarity over content tokens, or None when WordNet is unavailable."""

    def __init__(self):
        try:
            from nltk.corpus import wordnet
            wordnet.synsets("test")
            self.wordnet = wordnet
        except Exception:
            self.wordnet = None
        self.cache = {}

    def word_similarity(self, a, b):
        if a == b:
            return 1.0
        key = (a, b) if a < b else (b, a)
        if key in self.cache:
            return self.cache[key]
        best = 0.0
        # Maximum across senses; unrelated senses of a polysemous word can score high.
        for left in self.wordnet.synsets(key[0], pos="n"):
            for right in self.wordnet.synsets(key[1], pos="n"):
                score = left.wup_similarity(right) or 0.0
                if score > best:
                    best = score
        self.cache[key] = best
        return best

    def name_similarity(self, a, b):
        """Best alignment of content tokens, averaged in both directions."""
        if self.wordnet is None:
            return 0.0
        left = [t for t in tokens(a) if t not in STOPWORDS]
        right = [t for t in tokens(b) if t not in STOPWORDS]
        if not left or not right:
            return 0.0
        forward = sum(max(self.word_similarity(x, y) for y in right) for x in left) / len(left)
        backward = sum(max(self.word_similarity(y, x) for x in left) for y in right) / len(right)
        return (forward + backward) / 2


def links():
    found = set()
    for path in sorted(RESULTS.glob("*ary_pINDs.json")):
        text = TRAILING_COMMA.sub(r"\1", path.read_text(encoding="utf-8"))
        for relation in json.loads(text):
            for pind in relation.get("pINDs", []):
                dependent = ".".join(pind["dependant"])
                for ref in pind["referenced"]:
                    found.add((relation["relation"], dependent,
                               ref["relation"], ".".join(ref["attribute"])))
    return found


def main():
    catalog = json.loads((EXPORT / "catalog.json").read_text(encoding="utf-8"))
    stores = {e["relation"]: e["store"] for e in catalog if "store" in e}
    wordnet = WordNet()
    if wordnet.wordnet is None:
        print("WordNet is unavailable; the lexical-semantic class cannot be evaluated.")

    buckets = {"identical": [], "string-variant": [], "lexical-semantic": [], "unrelated": []}
    for dependent_set, dependent, referenced_set, referenced in sorted(links()):
        if store_of(dependent_set, stores) == store_of(referenced_set, stores):
            continue
        left, right = normalised(dependent), normalised(referenced)
        syntactic = syntactic_score(left, right)
        semantic = wordnet.name_similarity(dependent, referenced)
        entry = (f"{dependent_set}.{dependent}", f"{referenced_set}.{referenced}",
                 syntactic, semantic)
        if left == right:
            buckets["identical"].append(entry)
        elif syntactic >= STRING_VARIANT:
            buckets["string-variant"].append(entry)
        elif semantic >= LEXICAL:
            buckets["lexical-semantic"].append(entry)
        else:
            buckets["unrelated"].append(entry)

    total = sum(len(v) for v in buckets.values())
    print(f"{EXPORT.parent.name}: {total} cross-store links "
          f"(string-variant >= {STRING_VARIANT}, lexical-semantic >= {LEXICAL})\n")
    for name, items in buckets.items():
        share = len(items) / total if total else 0
        print(f"  {name:<17} {len(items):>6}  {share:>5.0%}")

    for name in ("lexical-semantic", "string-variant"):
        items = buckets[name]
        if not items:
            continue
        print(f"\n  {name}:")
        for dependent, referenced, syn, sem in sorted(items, key=lambda i: -i[3])[:10]:
            print(f"    syn {syn:.2f} sem {sem:.2f}  {dependent} -> {referenced}")
        if len(items) > 10:
            print(f"    ... and {len(items) - 10} more")


if __name__ == "__main__":
    main()
