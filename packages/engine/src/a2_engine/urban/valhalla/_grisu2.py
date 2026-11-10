"""Pinned RapidJSON Grisu2 digits for decimal-boundary coordinate echoes.

Adapted from RapidJSON internal/dtoa.h and internal/diyfp.h, copyright (C) 2015
THL A29 Limited, a Tencent company, and Milo Yip, under the MIT License:

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import math
import struct
from decimal import ROUND_DOWN, Decimal


def _normalize(significand: int, exponent: int) -> tuple[int, int]:
    shift = 64 - significand.bit_length()
    return significand << shift, exponent - shift


def _multiply(left: int, right: int) -> int:
    return (left * right + (1 << 63)) >> 64


def _round_digits(digits: list[int], delta: int, rest: int, ten_kappa: int, distance: int) -> None:
    while (
        rest < distance
        and delta - rest >= ten_kappa
        and (rest + ten_kappa < distance or distance - rest > rest + ten_kappa - distance)
    ):
        digits[-1] -= 1
        rest += ten_kappa


def coordinate_boundary_echo(value: float) -> float | None:
    """Reproduce the pinned writer for nonzero six-or-fewer-place coordinates.

    The caller restricts this fallback to decimal boundaries. Outside its two
    supported cached-power bins it fails closed, rather than extrapolating.
    """
    if not 0.000001 <= abs(value) <= 180:
        return None
    bits = struct.unpack(">Q", struct.pack(">d", abs(value)))[0]
    significand = (bits & ((1 << 52) - 1)) + (1 << 52)
    exponent = (bits >> 52) - 1075
    plus, boundary_exponent = _normalize((significand << 1) + 1, exponent - 1)
    if significand == 1 << 52:
        minus, minus_exponent = (significand << 2) - 1, exponent - 2
    else:
        minus, minus_exponent = (significand << 1) - 1, exponent - 1
    minus <<= minus_exponent - boundary_exponent
    index = (math.ceil((-61 - boundary_exponent) * 0.30102999566398114 + 347) >> 3) + 1
    powers = {44: (0x9C40000000000000, -50), 45: (0xE8D4A51000000000, -24)}
    if index not in powers:
        return None
    cached, cached_exponent = powers[index]
    normalized, _ = _normalize(significand, exponent)
    scaled = _multiply(normalized, cached)
    upper = _multiply(plus, cached) - 1
    lower = _multiply(minus, cached) + 1
    delta = upper - lower
    shift = -(boundary_exponent + cached_exponent + 64)
    one = 1 << shift
    integer, fraction = upper >> shift, upper & (one - 1)
    kappa = len(str(integer))
    decimal_exponent = 348 - (index << 3)
    digits: list[int] = []
    while kappa > 0:
        digit, integer = divmod(integer, 10 ** (kappa - 1))
        if digit or digits:
            digits.append(digit)
        kappa -= 1
        rest = (integer << shift) + fraction
        if rest <= delta:
            decimal_exponent += kappa
            _round_digits(digits, delta, rest, 10**kappa << shift, upper - scaled)
            break
    else:
        while True:
            fraction *= 10
            delta *= 10
            digit = fraction >> shift
            if digit or digits:
                digits.append(digit)
            fraction &= one - 1
            kappa -= 1
            if fraction < delta:
                decimal_exponent += kappa
                distance = (upper - scaled) * (10 ** (-kappa) if -kappa < 20 else 0)
                _round_digits(digits, delta, fraction, one, distance & ((1 << 64) - 1))
                break
    rendered = Decimal("".join(str(digit) for digit in digits)).scaleb(decimal_exponent)
    rendered = rendered.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    return math.copysign(float(rendered), value)
