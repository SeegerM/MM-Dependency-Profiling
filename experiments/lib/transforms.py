"""A small language of value transformations, shared by injection and discovery.

Stores often differ only in representation: a registry prefix (`0114709` / `tt0114709`),
zero-padding, letter case, a unit (`1.5` kg / `1500` g). Unlike edit distance, which also matches
neighbouring values such as consecutive identifiers, a transformation maps one representation onto
the other exactly, and inclusion is checked with equality after the mapping.

A transformation is a tuple of steps, applied left to right. Each step is (name, *params):

  ("strip_prefix", p)   remove a leading constant p (the value is left alone without it)
  ("add_prefix", p)     prepend a constant p
  ("strip_suffix", s)   remove a trailing constant s
  ("add_suffix", s)     append a constant s
  ("pad", w)            left-pad a digit string with zeros to width w
  ("unpad",)            strip leading zeros of a digit string (keeping a single 0)
  ("upper",) ("lower",) ("capitalize",)   letter case (capitalize: first letter upper, rest lower)
  ("affine", a, b)      numeric a * x + b; compared with a target's values numerically, rounded to
                        the target's decimal precision, so 1.5 kg -> 1500 g and 10.99 -> 1099 match

`spec` / `parse` give the text form used on command lines: steps joined by `|`, parameters by `:`,
e.g. `unpad|add_prefix:tt` or `affine:100:0`.
"""
from decimal import Decimal, InvalidOperation

STRING_STEPS = {"strip_prefix", "add_prefix", "strip_suffix", "add_suffix", "pad", "unpad", "upper",
                "lower", "capitalize"}


def spec(transform):
    return "|".join(":".join([step[0]] + [format_param(p) for p in step[1:]]) for step in transform)


def format_param(p):
    if isinstance(p, float):
        return repr(p).rstrip("0").rstrip(".") if "." in repr(p) else repr(p)
    return str(p)


def parse(text):
    steps = []
    for part in text.split("|"):
        name, *params = part.split(":")
        if name == "affine":
            steps.append((name, float(params[0]), float(params[1]) if len(params) > 1 else 0.0))
        elif name == "pad":
            steps.append((name, int(params[0])))
        else:
            steps.append((name, *params))
    return tuple(steps)


def to_number(value):
    try:
        return Decimal(value.strip())
    except (InvalidOperation, AttributeError, ValueError):
        return None


def decimals_of(values):
    """The largest number of decimal places among numeric values (their precision)."""
    best = 0
    for v in values:
        if "." in v:
            best = max(best, len(v.strip().split(".", 1)[1].rstrip()))
    return best


def apply_string(step, value):
    name = step[0]
    if name == "strip_prefix":
        return value[len(step[1]):] if value.startswith(step[1]) else value
    if name == "add_prefix":
        return step[1] + value
    if name == "strip_suffix":
        return value[:-len(step[1])] if step[1] and value.endswith(step[1]) else value
    if name == "add_suffix":
        return value + step[1]
    if name == "pad":
        return value.zfill(step[1]) if value.isdigit() else value
    if name == "unpad":
        return (value.lstrip("0") or "0") if value.isdigit() else value
    if name == "upper":
        return value.upper()
    if name == "lower":
        return value.lower()
    if name == "capitalize":
        return value.capitalize()
    raise ValueError(step)


def apply(transform, value, decimals=None):
    """Applies a transformation to one serialized value.

    An affine step returns the number formatted at `decimals` places (the target's precision), or
    None when the value is not numeric, so it never matches.
    """
    out = value
    for step in transform:
        if step[0] == "affine":
            x = to_number(out)
            if x is None:
                return None
            y = Decimal(repr(step[1])) * x + Decimal(repr(step[2]))
            places = 0 if decimals is None else decimals
            try:
                out = format(y.quantize(Decimal(1).scaleb(-places)), "f")
            except InvalidOperation:
                return None  # beyond the decimal context (a huge value): no image
        else:
            out = apply_string(step, out)
    return out


def canonical_target(values, numeric):
    """The target's values in the form transformed values are compared against."""
    if not numeric:
        return set(values)
    places = decimals_of(values)
    out = set()
    for v in values:
        x = to_number(v)
        if x is not None:
            out.add(format(x.quantize(Decimal(1).scaleb(-places)), "f"))
    return out


def is_numeric_transform(transform):
    return any(step[0] == "affine" for step in transform)
