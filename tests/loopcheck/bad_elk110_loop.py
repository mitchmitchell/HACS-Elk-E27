import hashlib


def f() -> bytes:
    return hashlib.pbkdf2_hmac("sha256", b"p", b"s", 1000)
