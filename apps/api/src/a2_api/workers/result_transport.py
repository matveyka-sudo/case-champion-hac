"""Lossless result frames with independent wire and decoded byte budgets."""

import struct
import zlib

DEFAULT_RESULT_BYTES = 16 * 1024 * 1024
MAX_DECODED_RESULT_BYTES = 32 * 1024 * 1024
MAX_EXPANSION_RATIO = 128
_PREFIX = b"A2RZ1\x00\r\n"
_HEADER = struct.Struct(">8sQ")
_CHUNK_BYTES = 64 * 1024


def validate_decoded_limit(limit: int) -> int:
    if type(limit) is not int or not DEFAULT_RESULT_BYTES <= limit <= MAX_DECODED_RESULT_BYTES:
        raise ValueError("Decoded result budget must be between 16 and 32 MiB")
    return limit


def _validate_wire_limit(limit: int) -> None:
    # The separately admitted historical DEMO path already allows 32 MiB wire.
    if limit not in (DEFAULT_RESULT_BYTES, MAX_DECODED_RESULT_BYTES):
        raise ValueError("Unknown result wire budget")


def encode_result(data: bytes, *, decoded_limit: int, wire_limit: int) -> bytes:
    validate_decoded_limit(decoded_limit)
    _validate_wire_limit(wire_limit)
    if len(data) > decoded_limit:
        raise ValueError("Calculation output exceeds decoded result budget")
    if len(data) <= wire_limit:
        return data
    compressed = zlib.compress(data, level=6)
    if len(data) > MAX_EXPANSION_RATIO * len(compressed):
        raise ValueError("Calculation output exceeds compression expansion budget")
    frame = _HEADER.pack(_PREFIX, len(data)) + compressed
    if len(frame) > wire_limit:
        raise ValueError("Calculation output exceeds configured transport limit")
    return frame


def decode_result(data: bytes, *, decoded_limit: int, wire_limit: int) -> bytes:
    validate_decoded_limit(decoded_limit)
    _validate_wire_limit(wire_limit)
    if len(data) > wire_limit:
        raise ValueError("Calculation output exceeds configured transport limit")
    if not data.startswith(b"A2R"):
        if len(data) > decoded_limit:
            raise ValueError("Calculation output exceeds decoded result budget")
        return data
    if len(data) <= _HEADER.size:
        raise ValueError("Incomplete compressed result header")
    prefix, declared = _HEADER.unpack_from(data)
    compressed = memoryview(data)[_HEADER.size :]
    if prefix != _PREFIX:
        raise ValueError("Unknown compressed result version")
    if declared > decoded_limit or declared > MAX_EXPANSION_RATIO * len(compressed):
        raise ValueError("Compressed result exceeds decoded or expansion budget")
    decoder = zlib.decompressobj()
    result = bytearray()
    try:
        for offset in range(0, len(compressed), _CHUNK_BYTES):
            # max_length bounds allocation even for a forged length or a bomb.
            result.extend(
                decoder.decompress(
                    compressed[offset : offset + _CHUNK_BYTES], declared - len(result) + 1
                )
            )
            if len(result) > declared or decoder.unconsumed_tail or decoder.unused_data:
                raise ValueError("Compressed result overflows or contains trailing data")
    except zlib.error as exc:
        raise ValueError("Invalid compressed result stream") from exc
    # No flush: its length argument is an allocation hint, not an output bound.
    if not decoder.eof or len(result) != declared:
        raise ValueError("Compressed result is truncated or differs from its declared length")
    return bytes(result)
