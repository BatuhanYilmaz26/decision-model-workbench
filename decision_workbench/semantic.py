"""Portable declared semantic links; authenticated adjudication lives in the platform."""

import copy
import hashlib
from .data import dumps


def row_hashes(rows):
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Semantic inputs require unique record identifiers")
    return {r["id"]: hashlib.sha256(dumps(r).encode()).hexdigest() for r in rows}


def linked_pairs(rows, *, included_only=False):
    ids = {r["id"] for r in rows}
    output = set()
    for row in rows:
        links = row["metadata"].get("semantic_links", [])
        if (
            not isinstance(links, list)
            or any(not isinstance(v, str) for v in links)
            or len(set(links)) != len(links)
        ):
            raise ValueError("semantic_links must be unique record identifiers")
        for other in links:
            if other == row["id"] or (other not in ids and not included_only):
                raise ValueError(
                    "A semantic link references itself or missing study data"
                )
            if other in ids:
                output.add(tuple(sorted((row["id"], other))))
    return sorted(output)


def attach_links(rows, pairs, binding):
    """Copy new records only; immutable base snapshot rows are never rewritten."""
    result = copy.deepcopy(rows)
    by_id = {r["id"]: r for r in result}
    for row in result:
        row["metadata"]["semantic_review"] = dict(binding)
    for left, right in pairs:
        for record, other in ((left, right), (right, left)):
            if record in by_id:
                links = by_id[record]["metadata"].setdefault("semantic_links", [])
                if other not in links:
                    links.append(other)
                links.sort()
    return result


def enforce_edges(splits, pairs=None):
    rows = [row for part in splits.values() for row in part]
    roles = {r["id"]: name for name, part in splits.items() for r in part}
    for left, right in pairs if pairs is not None else linked_pairs(rows):
        if left not in roles or right not in roles:
            raise ValueError("A semantic relationship references missing study data")
        if roles[left] != roles[right]:
            raise ValueError("Reviewed semantic relatives cross reserved dataset roles")
