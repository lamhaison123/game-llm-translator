"""Ruby Marshal 4.8 codec for RPG Maker XP/VX/VX Ace .rxdata/.rvdata/.rvdata2 files.

Vendored from RPGMTL (https://github.com/MizaGBF/RPGMTL) — MIT License,
copyright (c) MizaGBF. See LICENSE-RPGMTL for full notice.

Only the MC (Marshal Container) + ME (Marshal Element) codec is included.
Walkers/extractors live in the parent package's rpg_maker_vxace.py.

References:
- https://docs.ruby-lang.org/en/3.3/marshal_rdoc.html
- https://ruby-doc.org/core/Marshal.html
- https://github.com/ruby/ruby/blob/master/marshal.c
"""
from __future__ import annotations

import io
import math
import struct
from dataclasses import dataclass
from typing import Any, Callable, Iterator


class HashTableIter:
    """Iterate a Marshal hash table yielding (key, value) ME pairs."""

    def __init__(self, d: dict) -> None:
        self._dict = d
        self._keys = iter(d)

    def __iter__(self) -> Iterator:
        return self

    def __next__(self):
        key = next(self._keys)
        return key, self._dict[key][1]


@dataclass(slots=True)
class MC:
    """Marshal Container — owns symbol + object link tables for one stream."""

    root: "ME | None"
    symtable: list
    objtable: list

    def __init__(self) -> None:
        self.root = None
        self.symtable = []
        self.objtable = [None]

    @staticmethod
    def load(binary: bytes) -> "MC":
        with io.BytesIO(binary) as handle:
            if handle.read(1) != b"\x04" or handle.read(1) != b"\x08":
                raise ValueError("Unsupported Ruby Marshal version or invalid file")
            mc = MC()
            mc.root = mc._process_token(handle)
            return mc

    def dump(self) -> bytes:
        with io.BytesIO() as handle:
            handle.write(b"\x04\x08")
            self.root.dump(handle)
            return handle.getvalue()

    def _process_token(self, handle: io.BytesIO, ivar: bool = False) -> "ME":
        token = handle.read(1)
        if not token:
            raise ValueError("Unexpected EOF in Marshal stream")
        if token not in self.TOKEN_TABLE:
            raise ValueError(f"Unknown Marshal token {token!r}")
        # Reserve an object link slot before parsing children (link/symbol/ivar/fixnum/true/false/nil are exempt)
        if not ivar and token not in (b"0", b"T", b"F", b"i", b":", b";", b"@", b"I"):
            index = len(self.objtable)
            self.objtable.append(None)
        else:
            index = None
        me = self.TOKEN_TABLE[token](self, token, handle)
        if ivar:
            me.attributes = self._read_hashtable(b"{", handle)
            me.attributes.silent_token = True
        if not ivar and index is not None:
            self.objtable[index] = me
        return me

    @staticmethod
    def util_read_fixnum(handle: io.BytesIO) -> int:
        length = struct.unpack("b", handle.read(1))[0]
        if length == 0:
            return 0
        if 4 < length < 128:
            return length - 5
        if -129 < length < -4:
            return length + 5
        alen = abs(length)
        data = handle.read(alen)
        result = int.from_bytes(data, byteorder="little", signed=False)
        if length < 0:
            result -= 1 << (8 * alen)
        return result

    @staticmethod
    def util_write_fixnum(value: int) -> bytes:
        if value == 0:
            return b"\0"
        if 0 < value < 123:
            return struct.pack("b", value + 5)
        if -124 < value < 0:
            return struct.pack("b", value - 5)
        size = int(math.ceil(value.bit_length() / 8.0))
        if size > 5:
            raise ValueError(f"Fixnum {value} too large to serialize")
        back = value
        factor = 256 ** size
        if value < 0 and value == -factor:
            size -= 1
            value += factor // 256
        elif value < 0:
            value += factor
        sign = int(math.copysign(size, back))
        return struct.pack("b", sign) + value.to_bytes(size, byteorder="little", signed=False)

    # ---- token parsers ----

    def _read_nil(self, token, handle):
        return ME(self, token, None)

    def _read_true(self, token, handle):
        return ME(self, token, True)

    def _read_false(self, token, handle):
        return ME(self, token, False)

    def _read_instancevariable(self, token, handle):
        me = self._process_token(handle, ivar=True)
        # Reserve a slot for the ivar wrapper itself
        self.objtable.append(ME(self, b"I", False))
        return me

    def _read_string(self, token, handle):
        return ME(self, token, handle.read(self.util_read_fixnum(handle)))

    def _read_symbol(self, token, handle):
        me = ME(self, token, handle.read(self.util_read_fixnum(handle)))
        self.symtable.append(me)
        return me

    def _read_symlink(self, token, handle):
        me = ME(self, token, self.util_read_fixnum(handle))
        if me.data < 0 or me.data >= len(self.symtable):
            raise ValueError("Symlink out of range")
        return me

    def _read_fixnum(self, token, handle):
        return ME(self, token, self.util_read_fixnum(handle))

    def _read_array(self, token, handle):
        size = self.util_read_fixnum(handle)
        return ME(self, token, [self._process_token(handle) for _ in range(size)])

    def _read_hashtable(self, token, handle):
        me = ME(self, token, None)
        size = self.util_read_fixnum(handle)
        table: dict = {}
        for _ in range(size):
            key = self._process_token(handle)
            value = self._process_token(handle)
            table[key.at().data] = (key, value)
        me.data = table
        return me

    def _read_float(self, token, handle):
        return ME(self, token, handle.read(self.util_read_fixnum(handle)))

    def _read_bignum(self, token, handle):
        sign = handle.read(1)
        size = self.util_read_fixnum(handle)
        body = b"" if size == 0 else handle.read(2 * size)
        return ME(self, token, (sign, body))

    def _read_regex(self, token, handle):
        return ME(self, token, handle.read(self.util_read_fixnum(handle)))

    def _read_usermarshal(self, token, handle):
        return ME(self, token, (self._process_token(handle), self._process_token(handle)))

    def _read_object(self, token, handle):
        symbol = self._process_token(handle)
        table = self._read_hashtable(b"{", handle)
        table.silent_token = True
        return ME(self, token, (symbol, table))

    def _read_link(self, token, handle):
        me = ME(self, token, self.util_read_fixnum(handle))
        if me.data <= 0 or me.data >= len(self.objtable):
            raise ValueError("Object link out of range")
        return me

    def _read_userdefined(self, token, handle):
        return ME(self, token, (self._process_token(handle), handle.read(self.util_read_fixnum(handle))))

    def _read_classmodule(self, token, handle):
        return ME(self, token, handle.read(self.util_read_fixnum(handle)))

    TOKEN_TABLE = {
        b"0": _read_nil,
        b"T": _read_true,
        b"F": _read_false,
        b"I": _read_instancevariable,
        b'"': _read_string,
        b":": _read_symbol,
        b";": _read_symlink,
        b"i": _read_fixnum,
        b"[": _read_array,
        b"{": _read_hashtable,
        b"f": _read_float,
        b"l": _read_bignum,
        b"/": _read_regex,
        b"U": _read_usermarshal,
        b"o": _read_object,
        b"@": _read_link,
        b"u": _read_userdefined,
        b"m": _read_classmodule,
        b"c": _read_classmodule,
        b"M": _read_classmodule,
    }


@dataclass(slots=True)
class ME:
    """One Marshal Element. `token` is the type tag; `data` is the parsed payload."""

    owner: MC
    token: bytes
    data: Any
    attributes: "ME | None"
    silent_token: bool
    _dump_call: Callable

    def __init__(self, owner: MC, token: bytes, data: Any = None) -> None:
        self.owner = owner
        self.token = token
        self.data = data
        self.attributes = None
        self.silent_token = False
        self._dump_call = self.DUMP_CALL_TABLE.get(token, self._dump_unimplemented)

    def __repr__(self) -> str:
        return f"{self.token.decode()}:{self.data!r}"

    def __hash__(self) -> int:
        return hash(self.data)

    def __iter__(self):
        match self.token:
            case b"[":
                return iter(self.data)
            case b"{":
                return HashTableIter(self.data)
            case b"o":
                return self.data[1].__iter__()
            case b";" | b"@":
                return self.at().__iter__()
            case _:
                raise TypeError(f"ME token {self.token!r} not iterable")

    def __contains__(self, key) -> bool:
        match self.token:
            case b"[":
                return key in self.data
            case b"{":
                return key in self.data
            case b"o":
                return key in self.data[1]
            case b";" | b"@":
                return key in self.at()
            case _:
                raise TypeError(f"ME token {self.token!r} doesn't support 'in'")

    def get(self, key):
        match self.token:
            case b"[":
                return self.data[key]
            case b"{":
                return self.data.get(key, (None, None))[1]
            case b"o":
                return self.data[1].get(key)
            case b";" | b"@":
                return self.at().get(key)

    def at(self) -> "ME":
        match self.token:
            case b";":
                return self.owner.symtable[self.data]
            case b"@":
                return self.owner.objtable[self.data]
            case _:
                return self

    def dump(self, handle: io.BytesIO) -> None:
        if self.attributes is not None:
            handle.write(b"I")
        if not self.silent_token:
            handle.write(self.token)
        self._dump_call(self, handle)
        if self.attributes is not None:
            self.attributes.dump(handle)

    # ---- dumpers ----

    def _dump_none(self, handle):
        pass

    def _dump_binary(self, handle):
        handle.write(self.owner.util_write_fixnum(len(self.data)))
        handle.write(self.data)

    def _dump_fixnum(self, handle):
        handle.write(self.owner.util_write_fixnum(self.data))

    def _dump_array(self, handle):
        handle.write(self.owner.util_write_fixnum(len(self.data)))
        for e in self.data:
            e.dump(handle)

    def _dump_hashtable(self, handle):
        handle.write(self.owner.util_write_fixnum(len(self.data)))
        for _, (key, value) in self.data.items():
            key.dump(handle)
            value.dump(handle)

    def _dump_object(self, handle):
        self.data[0].dump(handle)
        self.data[1].dump(handle)

    def _dump_bignum(self, handle):
        handle.write(self.data[0])
        handle.write(self.owner.util_write_fixnum(len(self.data[1]) // 2))
        handle.write(self.data[1])

    def _dump_pair(self, handle):
        self.data[0].dump(handle)
        self.data[1].dump(handle)

    def _dump_userdefined(self, handle):
        self.data[0].dump(handle)
        handle.write(self.owner.util_write_fixnum(len(self.data[1])))
        handle.write(self.data[1])

    def _dump_unimplemented(self, handle):
        raise ValueError(f"Cannot dump Marshal token {self.token!r}")

    DUMP_CALL_TABLE = {
        b"0": _dump_none,
        b"T": _dump_none,
        b"F": _dump_none,
        b'"': _dump_binary,
        b":": _dump_binary,
        b";": _dump_fixnum,
        b"i": _dump_fixnum,
        b"[": _dump_array,
        b"{": _dump_hashtable,
        b"f": _dump_binary,
        b"l": _dump_bignum,
        b"/": _dump_binary,
        b"U": _dump_pair,
        b"o": _dump_object,
        b"@": _dump_fixnum,
        b"u": _dump_userdefined,
        b"m": _dump_binary,
        b"c": _dump_binary,
        b"M": _dump_binary,
    }
