"""A comparable identity for a discovered or documented dependency, at any arity.

A dependency is keyed by the pair of scopes it relates and the *set* of attribute pairs it maps
between them, so reordering a composite side does not change the key. Scope names are normalised
by dropping store prefixes (`neo4j:`) and array paths (`@...`), since producers and oracles spell
the same extractor differently. Nothing else is normalised.
"""
import re

SCOPE_SUFFIX = re.compile(r"@[^.]*")
COMPOSITE = re.compile(r"^(.*)\.\(([^)]*)\)$")


def _scope_and_attributes(side):
    """Splits one side into its scope name and its attribute list.

    Accepts both shapes the codebase uses: `scope.attribute` for a unary side and
    `scope.(a,b)` for a composite one, the latter being how the oracles write a composite key.
    """
    composite = COMPOSITE.match(side)
    if composite:
        scope, attributes = composite.group(1), composite.group(2)
        return scope, [a.strip() for a in attributes.split(",") if a.strip()]
    scope, _, attribute = side.rpartition(".")
    return scope, [attribute]


ENDPOINT = ("source", "target")


def _canonical_scope(scope):
    """The scope's own name, without the array path it was lifted through or its store prefix.

    Normally the last path segment. For a graph relationship projected onto its endpoints
    (`<store>:-[TYPE]->.source`) the last segment is the endpoint, so the relationship type before
    it is kept too; otherwise all relationship types would collapse into one scope.
    """
    cleaned = SCOPE_SUFFIX.sub("", scope)
    parts = [p for p in cleaned.split(".") if p]
    if not parts:
        return cleaned.split(":")[-1]
    if len(parts) >= 2 and parts[-1] in ENDPOINT:
        return (parts[-2].split(":")[-1] + "." + parts[-1])
    return parts[-1].split(":")[-1]


def key(dependent, referenced):
    """A hashable identity for the dependency `dependent -> referenced`.

    Returns (dependent scope, referenced scope, frozenset of attribute pairs). Sides of differing
    arity produce a key that cannot match anything.
    """
    dependent_scope, dependent_attributes = _scope_and_attributes(dependent)
    referenced_scope, referenced_attributes = _scope_and_attributes(referenced)
    if len(dependent_attributes) != len(referenced_attributes):
        return (_canonical_scope(dependent_scope), _canonical_scope(referenced_scope),
                frozenset({("<arity mismatch>", dependent + "->" + referenced)}))
    return (_canonical_scope(dependent_scope), _canonical_scope(referenced_scope),
            frozenset(zip(dependent_attributes, referenced_attributes)))


def key_from_lists(dependent_scope, dependent_attributes, referenced_scope, referenced_attributes):
    """The same identity, built from a result file's already-split attribute lists."""
    if len(dependent_attributes) != len(referenced_attributes):
        return (_canonical_scope(dependent_scope), _canonical_scope(referenced_scope),
                frozenset({("<arity mismatch>", str(dependent_attributes))}))
    return (_canonical_scope(dependent_scope), _canonical_scope(referenced_scope),
            frozenset(zip(dependent_attributes, referenced_attributes)))


def arity(dependent, referenced):
    """How many attribute correspondences the dependency carries."""
    _, attributes = _scope_and_attributes(dependent)
    return len(attributes)
