import os
import time


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_base32_crockford(num: int, length: int) -> str:
    chars = []
    for _ in range(length):
        num, rem = divmod(num, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def ulid() -> str:
    
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_hi = int.from_bytes(os.urandom(8), "big")
    rand_lo = int.from_bytes(os.urandom(2), "big")
    rand = (rand_hi << 16) | rand_lo

    ts_str = _encode_base32_crockford(ts_ms, 10)
    rand_str = _encode_base32_crockford(rand, 16)
    return ts_str + rand_str
