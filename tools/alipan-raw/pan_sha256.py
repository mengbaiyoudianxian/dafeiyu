"""Checkpointable SHA-256 for the Ubuntu OpenSSL 3 backup host."""

import ctypes as C
import hashlib
import re
import struct


class _Context(C.Structure):
    # openssl/sha.h, SHA256_CTX. SHA_LONG is always an unsigned 32-bit word.
    _fields_ = [
        ("h", C.c_uint32 * 8),
        ("Nl", C.c_uint32), ("Nh", C.c_uint32),
        ("data", C.c_uint32 * 16),
        ("num", C.c_uint), ("md_len", C.c_uint),
    ]


_lib = C.CDLL("libcrypto.so.3")
_lib.OPENSSL_version_major.argtypes = []
_lib.OPENSSL_version_major.restype = C.c_uint
if (_lib.OPENSSL_version_major() != 3 or C.sizeof(_Context) != 112
        or _Context.data.offset != 40 or _Context.num.offset != 104):
    raise RuntimeError("Unsupported OpenSSL SHA256_CTX ABI")
_lib.SHA256_Init.argtypes = [C.POINTER(_Context)]
_lib.SHA256_Update.argtypes = [C.POINTER(_Context), C.c_char_p, C.c_size_t]
_lib.SHA256_Final.argtypes = [C.POINTER(C.c_ubyte), C.POINTER(_Context)]
for _op in (_lib.SHA256_Init, _lib.SHA256_Update, _lib.SHA256_Final):
    _op.restype = C.c_int


class PanSHA256:
    """Store chaining words and up to 63 unprocessed bytes in an opaque checkpoint."""

    FORMAT = "pan-sha256-v1"

    def __init__(self):
        self._ctx = _Context()
        if _lib.SHA256_Init(C.byref(self._ctx)) != 1 or self._ctx.md_len != 32:
            raise RuntimeError("SHA256_Init failed")
        self.length = 0

    def update(self, chunk):
        if not isinstance(chunk, (bytes, memoryview, bytearray)):
            raise TypeError("expected bytes-like input")
        if not isinstance(chunk, bytes):
            chunk = bytes(chunk)
        if self.length + len(chunk) >= 1 << 61:
            raise OverflowError("SHA-256 input exceeds length field")
        if _lib.SHA256_Update(C.byref(self._ctx), chunk, len(chunk)) != 1:
            raise RuntimeError("SHA256_Update failed")
        self.length += len(chunk)

    def hexdigest(self):
        ctx = _Context.from_buffer_copy(self._ctx)
        digest = (C.c_ubyte * 32)()
        if _lib.SHA256_Final(digest, C.byref(ctx)) != 1:
            raise RuntimeError("SHA256_Final failed")
        return bytes(digest).hex()

    @classmethod
    def _check(cls, length, state):
        return hashlib.sha256(cls.FORMAT.encode("ascii") + struct.pack(">Q", length) + state).hexdigest()

    def checkpoint(self):
        ctx = self._ctx
        if (ctx.num != self.length % 64 or ctx.md_len != 32
                or (ctx.Nh << 32 | ctx.Nl) != self.length * 8):
            raise RuntimeError("SHA-256 context byte count mismatch")
        state = struct.pack(">8I", *ctx.h) + bytes(ctx.data)[:ctx.num]
        return {"format": self.FORMAT, "length": self.length, "state": state.hex(),
                "check": self._check(self.length, state)}

    @classmethod
    def restore(cls, snapshot, expected_length=None):
        if not isinstance(snapshot, dict) or snapshot.get("format") != cls.FORMAT:
            raise ValueError("unknown SHA-256 checkpoint format")
        length = snapshot.get("length")
        if type(length) is not int or not 0 <= length < 1 << 61:
            raise ValueError("invalid checkpoint length")
        if expected_length is not None and (type(expected_length) is not int or length != expected_length):
            raise ValueError("checkpoint and backup position differ")
        encoded = snapshot.get("state")
        if (not isinstance(encoded, str) or len(encoded) != (32 + length % 64) * 2
                or not re.fullmatch("[0-9a-f]+", encoded)):
            raise ValueError("invalid SHA-256 checkpoint state")
        state = bytes.fromhex(encoded)
        if snapshot.get("check") != cls._check(length, state):
            raise ValueError("SHA-256 checkpoint checksum mismatch")
        instance = cls()
        ctx = instance._ctx
        ctx.h[:] = struct.unpack(">8I", state[:32])
        ctx.Nl, ctx.Nh = (length * 8) & 0xffffffff, (length * 8) >> 32
        ctx.num = length % 64
        if ctx.num:
            C.memmove(C.addressof(ctx) + _Context.data.offset, state[32:], ctx.num)
        instance.length = length
        return instance


def _self_test():
    import json
    ref = hashlib.sha256()
    actual = PanSHA256()
    for size in (0, 1, 62, 1, 63, 127, 128, 1001, 4096):
        chunk = bytes((i % 251 for i in range(size)))
        ref.update(chunk)
        actual.update(chunk)
        assert actual.hexdigest() == ref.hexdigest()
        snap = json.loads(json.dumps(actual.checkpoint()))
        actual = PanSHA256.restore(snap, expected_length=actual.length)
        assert actual.hexdigest() == ref.hexdigest()
    for change in ({"format": "bad"}, {"length": -1}, {"length": True},
                   {"state": "bad"}, {"check": "bad"}):
        try:
            PanSHA256.restore({**snap, **change})
        except ValueError:
            pass
        else:
            raise AssertionError("invalid checkpoint accepted")
    try:
        PanSHA256.restore(snap, expected_length=0)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid position accepted")
    ref = hashlib.sha256()
    actual = PanSHA256()
    chunk = bytes(range(256)) * 4096
    for _ in range(512):
        actual.update(chunk)
        ref.update(chunk)
    actual = PanSHA256.restore(actual.checkpoint(), expected_length=512 << 20)
    actual.update(b"after 512 MiB")
    ref.update(b"after 512 MiB")
    assert ref.hexdigest() == actual.hexdigest()
    print("PASS OpenSSL3 SHA-256, checkpoint restoration, corrupt state rejection, 512 MiB boundary")


if __name__ == "__main__":
    _self_test()
