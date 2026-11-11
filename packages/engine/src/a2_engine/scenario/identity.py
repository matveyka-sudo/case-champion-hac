"""Canonical immutable identities, including frozen configuration mappings."""

import hashlib
import json
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from fractions import Fraction
from math import isfinite


def canonical(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: canonical(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, (Fraction, Decimal)):
        return str(value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise ValueError("UNSUPPORTED_SCENARIO_CANONICAL_VALUE")


def scenario_hash(value: object) -> str:
    """Hash the unchanged canonical JSON stream without expanding shared networks.

    Repeated frozen dataclass instances may represent one large immutable network
    referenced by thousands of search results. Cache their encoded fragments for
    this call only; each occurrence still feeds all of its original bytes to SHA.
    The bounded cache avoids retaining unique full search results or a root JSON
    string. Public canonical() and the stable v1 identity bytes remain unchanged.
    """
    counts: dict[int, int] = {}
    seen: set[int] = set()

    def count_shared(item: object) -> None:
        if is_dataclass(item) and not isinstance(item, type):
            identity = id(item)
            if getattr(type(item), "__dataclass_params__").frozen:
                counts[identity] = counts.get(identity, 0) + 1
            if identity in seen:
                return
            seen.add(identity)
            for entry in fields(item):
                count_shared(getattr(item, entry.name))
        elif isinstance(item, (Mapping, tuple, list, set, frozenset)):
            if id(item) in seen:
                return
            seen.add(id(item))
            children = item.values() if isinstance(item, Mapping) else item
            for child in children:
                count_shared(child)

    count_shared(value)
    repeated = {identity for identity, count in counts.items() if count > 1}
    counts.clear()
    seen.clear()
    cache: OrderedDict[int, tuple[object, bytes]] = OrderedDict()
    cache_bytes = 0
    max_fragment = 8 * 1024 * 1024
    max_cache = 32 * 1024 * 1024

    def scalar(item: object) -> bytes:
        return json.dumps(item, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    def raw_chunks(item: object) -> Iterator[bytes]:
        if is_dataclass(item) and not isinstance(item, type):
            yield b"{"
            for index, entry in enumerate(sorted(fields(item), key=lambda entry: entry.name)):
                if index:
                    yield b","
                yield scalar(entry.name)
                yield b":"
                yield from chunks(getattr(item, entry.name))
            yield b"}"
        elif isinstance(item, Mapping):
            # canonical() sorts original keys before converting to strings; a
            # collision retains the last value. Final JSON sorts string keys.
            normalized = {str(key): child for key, child in sorted(item.items())}
            if len(normalized) != len(item):
                # Legacy canonicalization validates even overwritten children,
                # in original-key order. Preserve that rare collision path.
                validated = canonical(item)
                assert isinstance(validated, dict)
                normalized = validated
            yield b"{"
            for index, key in enumerate(sorted(normalized)):
                if index:
                    yield b","
                yield scalar(key)
                yield b":"
                yield from chunks(normalized[key])
            yield b"}"
        elif isinstance(item, (tuple, list, set, frozenset)):
            children: Iterable[object]
            if isinstance(item, (set, frozenset)):
                # Set order uses json.dumps DEFAULT spaces, exactly as v1.
                normalized_set = canonical(item)
                assert isinstance(normalized_set, list)
                children = normalized_set
            else:
                children = item
            yield b"["
            for index, child in enumerate(children):
                if index:
                    yield b","
                yield from chunks(child)
            yield b"]"
        elif isinstance(item, (date, datetime, time)):
            yield scalar(item.isoformat())
        elif isinstance(item, (Fraction, Decimal)):
            yield scalar(str(item))
        elif item is None or isinstance(item, (str, int, bool)):
            yield scalar(item)
        elif isinstance(item, float) and isfinite(item):
            yield scalar(item)
        else:
            raise ValueError("UNSUPPORTED_SCENARIO_CANONICAL_VALUE")

    def chunks(item: object) -> Iterator[bytes]:
        nonlocal cache_bytes
        identity = id(item)
        found = cache.get(identity)
        if found is not None and found[0] is item:
            cache.move_to_end(identity)
            yield found[1]
            return
        capture = bytearray() if identity in repeated else None
        for fragment in raw_chunks(item):
            if capture is not None:
                if len(capture) + len(fragment) <= max_fragment:
                    capture.extend(fragment)
                else:
                    capture = None
            yield fragment
        if capture is not None:
            encoded = bytes(capture)
            while cache and cache_bytes + len(encoded) > max_cache:
                _, (_, removed) = cache.popitem(last=False)
                cache_bytes -= len(removed)
            cache[identity] = (item, encoded)
            cache_bytes += len(encoded)

    digest = hashlib.sha256()
    for fragment in chunks(value):
        digest.update(fragment)
    return digest.hexdigest()


def collect_evidence(value: object) -> tuple[str, ...]:
    found: set[str] = set()
    visited: set[int] = set()

    def walk(item: object) -> None:
        if item is None or isinstance(item, (str, int, float, bool)) or id(item) in visited:
            return
        visited.add(id(item))
        if is_dataclass(item) and not isinstance(item, type):
            for entry in fields(item):
                child = getattr(item, entry.name)
                if entry.name.endswith("_refs") and isinstance(
                    child, (tuple, list, set, frozenset)
                ):
                    found.update(v for v in child if isinstance(v, str))
                walk(child)
        elif isinstance(item, Mapping):
            for child in item.values():
                walk(child)
        elif isinstance(item, (tuple, list, set, frozenset)):
            for child in item:
                walk(child)

    walk(value)
    return tuple(sorted(found))
