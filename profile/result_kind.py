"""Sorts discovered inclusions into the kinds of result they actually are.

The kinds, in the order they are tested:

  strict reference     the referenced side is a candidate key (the join view; the only kind
                       fairly scored against a foreign-key oracle)
  shared domain        holds in both directions with high coverage and comparable cardinality,
                       e.g. `patent.country` and `inventor.country`
  cross-snapshot       coverage too low to be a constraint, but an overlap far larger than chance
  correspondence       (e.g. thousands of shared opaque identifiers)
  general inclusion    everything else; kept as profiling evidence, hidden from the join view

Nothing is discarded; the kinds only determine what the join view shows.
"""
import math


def _log10_binomial_tail(trials, probability, successes):
    """-log10 P[Bin(trials, probability) >= successes], the surprise of an overlap this large.

    Asks whether this many shared values would arise if the dependent's values were drawn from the
    comparison domain independently of the referenced side.
    """
    if successes <= 0:
        return 0.0
    if probability <= 0.0:
        return float("inf")
    if probability >= 1.0:
        return 0.0
    # Normal approximation with continuity correction; adequate for ranking and does not underflow.
    mean = trials * probability
    if successes <= mean:
        return 0.0
    variance = trials * probability * (1.0 - probability)
    if variance <= 0.0:
        return float("inf")
    z = (successes - 0.5 - mean) / math.sqrt(variance)
    # log10 of the upper tail of the standard normal, via the Mills-ratio bound for large z.
    if z < 6.0:
        tail = 0.5 * math.erfc(z / math.sqrt(2.0))
        return -math.log10(tail) if tail > 0 else float("inf")
    return (z * z / 2.0) / math.log(10.0) + math.log10(z * math.sqrt(2 * math.pi))


MINIMUM_SUPPORT = 25


def near_key(distinct, filled, rows, degree=0.99):
    """Whether an observation is a theta-near-key: unique enough and present enough.

    Relaxes uniqueness and presence by the same theta; at theta = 1 this is an exact key.
    Relaxing uniqueness alone would admit a mostly-null column.
    """
    if not filled or not distinct or not rows:
        return False
    return distinct / filled >= degree and filled / rows >= degree


def qualifies_as_reference_target(distinct, filled, rows, degree=0.99,
                                  minimum_support=MINIMUM_SUPPORT):
    """Whether a referenced side is a candidate key: unique enough, present enough, over enough rows.

    Uniqueness and presence are relaxed (see near_key), since real keys in dirty data can be
    slightly less than 100% unique. The `minimum_support` floor on non-null values guards against
    accidental uniqueness in tiny scopes, where observing no duplicate is uninformative; it can
    also drop genuine keys of small lookup tables, so pass minimum_support=0 to favour recall.
    """
    if filled < minimum_support:
        return False
    return near_key(distinct, filled, rows, degree)


def classify(dependent_distinct, referenced_distinct, overlap, coverage,
             referenced_is_key, reverse_holds, domain_size,
             shared_domain_coverage=0.9, cardinality_tolerance=0.5,
             correspondence_surprise=6.0, correspondence_coverage=0.5):
    """The kind of result one inclusion is, plus the evidence the decision rested on.

    `domain_size` is the number of distinct values in the comparison domain of the two sides; the
    cross-snapshot test is skipped without it.
    """
    evidence = {"coverage": coverage, "overlap": overlap,
                "dependent_distinct": dependent_distinct,
                "referenced_distinct": referenced_distinct}

    if referenced_is_key:
        return "strict reference", evidence

    if reverse_holds and coverage >= shared_domain_coverage and dependent_distinct and \
            referenced_distinct:
        ratio = min(dependent_distinct, referenced_distinct) / \
            max(dependent_distinct, referenced_distinct)
        evidence["cardinality_ratio"] = ratio
        if ratio >= cardinality_tolerance:
            return "shared domain", evidence

    if domain_size and overlap:
        # Under the null each dependent value lands in supp(R) with probability |supp(R)|/|U|.
        probability = min(1.0, referenced_distinct / domain_size)
        surprise = _log10_binomial_tail(dependent_distinct, probability, overlap)
        evidence["overlap_surprise"] = surprise
        # Coverage is capped so that ordinary high-coverage links are not collected here.
        if coverage < correspondence_coverage and surprise >= correspondence_surprise:
            return "cross-snapshot correspondence", evidence

    return "general inclusion", evidence


def join_view(kinds):
    """The kinds a reader asking "what can I join on" should be shown."""
    return {name for name in kinds if name == "strict reference"}


def overlap_correspondences(values, domain_size, reported,
                            minimum_overlap=100, surprise=12.0, maximum_coverage=0.5):
    """Finds correspondences an inclusion threshold cannot reach, on overlap evidence instead.

    Pairs below the discovery threshold are scored by the surprise of their intersection under the
    comparison domain, not by coverage, with a floor on the absolute overlap.

    Returns (dependent, referenced, coverage, overlap, surprise), strongest first, for pairs the
    discovery run did not already report.
    """
    names = [name for name, value_set in values.items() if len(value_set) >= minimum_overlap]
    found = []
    for dependent in names:
        left = values[dependent]
        for referenced in names:
            if dependent == referenced or (dependent, referenced) in reported:
                continue
            right = values[referenced]
            shared = len(left & right)
            if shared < minimum_overlap:
                continue
            coverage = shared / len(left)
            if coverage >= maximum_coverage:
                continue
            probability = min(1.0, len(right) / domain_size) if domain_size else 1.0
            score = _log10_binomial_tail(len(left), probability, shared)
            if score >= surprise:
                found.append((dependent, referenced, coverage, shared, score))
    found.sort(key=lambda row: -row[4])
    return found


# Alias for qualifies_as_reference_target (not near_key).
is_key = qualifies_as_reference_target
