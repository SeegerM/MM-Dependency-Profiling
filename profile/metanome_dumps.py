"""Reads the FD and UCC dumps the Metanome adapters write.

Metanome qualifies every column with its relation name, and the name of a materialized join itself
contains column strings, so columns are recognised by the longest matching suffix.
"""
import re

FD_LINE = re.compile(r"^\[(.*)\]\s*->\s*(.*?)(?:#([0-9.]+))?$")
UCC_LINE = re.compile(r"^\[(.*)\]\s*(?:#([0-9.]+))?$")


def strip_prefix(token, columns):
    for column in sorted(columns, key=len, reverse=True):
        if token == column or token.endswith("." + column):
            return column
    return token.strip()


def read_fds(path, columns):
    """(determinant tuple, dependent, degree) per line."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = FD_LINE.match(line.strip())
        if not match:
            continue
        determinant = [strip_prefix(t, columns) for t in match.group(1).split(",") if t.strip()]
        out.append((tuple(determinant), strip_prefix(match.group(2), columns),
                    float(match.group(3) or 1.0)))
    return out


def read_uccs(path, columns):
    """One tuple of column names per unique column combination."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = UCC_LINE.match(line.strip())
        if not match:
            continue
        out.append(tuple(strip_prefix(t, columns) for t in match.group(1).split(",") if t.strip()))
    return out


def read_beside(export, sets):
    """{relation -> (fds, uccs)} from the .fds and .uccs files beside each observation set.

    Absent dumps are not an error; the profile then has no FD- or UCC-derived records.
    """
    fds, uccs = {}, {}
    for relation, data in sets.items():
        columns = data["columns"]
        fd_path = data["path"].with_suffix(".fds") if "path" in data else None
        ucc_path = data["path"].with_suffix(".uccs") if "path" in data else None
        if fd_path is not None and fd_path.exists():
            fds[relation] = read_fds(fd_path, columns)
        if ucc_path is not None and ucc_path.exists():
            uccs[relation] = read_uccs(ucc_path, columns)
    return fds, uccs
