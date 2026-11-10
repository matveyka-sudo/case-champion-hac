"""TEST_ONLY oversized canonical output, with no routing or provider calls."""

from hashlib import sha256

from v2_handlers import success


def large_v2(claim):
    saved = success(claim)
    limitations = [
        f"TEST_ONLY-transport-{index:06}:" + sha256(str(index).encode()).hexdigest()
        for index in range(200000)
    ]
    return saved.model_copy(update={"limitations": [*saved.limitations, *limitations]})
