"""The compression Zandronum's query packets use.

Zandronum servers answer launcher queries in a Huffman code with a fixed tree. This is a
small Python port of the decoder and encoder in Zandronum's own `src/huffman` folder
(huffman.cpp, huffcodec.cpp, bitwriter.cpp and bitreader.cpp by Timothy Landers, MIT
licence), written from that source and checked against it: the tests compile the original
C++ codec when a compiler is present and compare outputs on random data.

Format, as in the source:

* The first byte is how many bits at the end of the last byte are unused (0 to 7).
* The codes follow, most significant bit of each code first, but every byte after the
  first is stored bit-reversed ("reversed bytes" mode of the original code), so reading
  a byte's bits from least to most significant gives the code bits in order.
* A packet whose first byte is 0xFF is not compressed: the rest is the data. (A real
  compressed packet never starts with 0xFF, since its first byte is at most 7.)

Nothing here touches Qt or the network.
"""

from __future__ import annotations

# The tree, serialised exactly as in Zandronum's huffman.cpp (`compatible_huffman_tree`).
# Each internal node is one byte whose low two bits say, for the left (bit 0) and right
# (bit 1) child, whether it is a leaf (1: the next byte is its value) or another internal
# node (0: its description follows, left subtree first).
_TREE = bytes([
      0,   0,   0,   1, 128,   0,   0,   0,   3,  38,  34,   2,   1,  80,   3, 110,
    144,  67,   0,   2,   1,  74,   3, 243, 142,  37,   2,   3, 124,  58, 182,   0,
      0,   1,  36,   0,   3, 221, 131,   3, 245, 163,   1,  35,   3, 113,  85,   0,
      1,  41,   1,  77,   3, 199, 130,   0,   1, 206,   3, 185, 153,   3,  70, 118,
      0,   3,   3,   5,   0,   0,   1,  24,   0,   2,   3, 198, 190,  63,   2,   3,
    139, 186,  75,   0,   1,  44,   2,   3, 240, 218,  56,   3,  40,  39,   0,   0,
      2,   2,   3, 244, 247,  81,  65,   0,   3,   9, 125,   3,  68,  60,   0,   0,
      1,  25,   3, 191, 138,   3,  86,  17,   0,   1,  23,   3, 220, 178,   2,   3,
    165, 194,  14,   1,   0,   2,   2,   0,   0,   2,   1, 208,   3, 150, 157, 181,
      1, 222,   2,   3, 216, 230, 211,   0,   2,   2,   3, 252, 141,  10,  42,   0,
      2,   3, 134, 135, 104,   1, 103,   3, 187, 225,  95,  32,   0,   0,   0,   0,
      0,   0,   1,  57,   1,  61,   3, 183, 237,   0,   0,   3, 233, 234,   3, 246,
    203,   2,   3, 250, 147,  79,   1, 129,   0,   1,   7,   3, 143, 136,   1,  20,
      3, 179, 148,   0,   0,   0,   3,  28, 106,   3, 101,  87,   1,  66,   0,   3,
    180, 219,   3, 227, 241,   0,   1,  26,   1, 251,   3, 229, 214,   3,  54,  69,
      0,   0,   0,   0,   0,   3, 231, 212,   3, 156, 176,   3,  93,  83,   0,   3,
     96, 253,   3,  30,  13,   0,   0,   2,   3, 175, 254,  94,   3, 159,  27,   2,
      1,   8,   3, 204, 226,  78,   0,   0,   0,   3, 107,  88,   1,  31,   3, 137,
    169,   2,   2,   3, 215, 145,   6,   4,   1, 127,   0,   1,  99,   3, 209, 217,
      0,   3, 213, 238,   3, 177, 170,   1, 132,   0,   0,   0,   2,   3,  22,  12,
    114,   2,   2,   3, 158, 197,  97,  45,   0,   1,  46,   1, 112,   3, 174, 249,
      0,   3, 224, 102,   2,   3, 171, 151, 193,   0,   0,   0,   3,  15,  16,   3,
      2, 168,   1,  49,   3,  91, 146,   0,   1,  48,   3, 173,  29,   0,   3,  19,
    126,   3,  92, 242,   0,   0,   0,   0,   0,   0,   3, 205, 192,   2,   3, 235,
    149, 255,   2,   3, 223, 184, 248,   0,   0,   3, 108, 236,   3, 111,  90,   2,
      3, 117, 115,  71,   0,   0,   3,  11,  50,   0,   3, 188, 119,   1, 122,   3,
    167, 162,   1, 160,   1, 133,   3, 123,  21,   0,   0,   2,   1,  59,   2,   3,
    155, 154,  98,  43,   0,   3,  76,  51,   2,   3, 201, 116,  72,   2,   0,   2,
      3, 109, 100, 121,   2,   3, 195, 232,  18,   1,   0,   2,   0,   1, 164,   2,
      3, 120, 189,  73,   0,   1, 196,   3, 239, 210,   3,  64,  62,  89,   0,   0,
      1,  33,   2,   3, 228, 161,  55,   2,   3,  84, 152,  47,   0,   0,   2,   3,
    207, 172, 140,   3,  82, 166,   0,   3,  53, 105,   1,  52,   3, 202, 200,
])

UNCOMPRESSED = 0xFF


def _build(data: bytes, index: int = 0):
    """Read one node at `index`. Returns (node, next_index); a node is a leaf value (int) or
    a (left, right) pair."""
    desc = data[index]
    index += 1
    children = []
    for bit in (0, 1):
        if desc & (1 << bit):
            children.append(data[index])
            index += 1
        else:
            child, index = _build(data, index)
            children.append(child)
    return (children[0], children[1]), index


_ROOT, _END = _build(_TREE)
if _END != len(_TREE):  # the table above must be used up exactly
    raise ImportError("the Huffman tree table is damaged")


def _codes() -> dict[int, tuple[int, int]]:
    """value -> (code, bit count) for every leaf."""
    table: dict[int, tuple[int, int]] = {}
    stack = [(_ROOT, 0, 0)]
    while stack:
        node, code, count = stack.pop()
        if isinstance(node, int):
            table[node] = (code, count)
        else:
            stack.append((node[0], code << 1, count + 1))
            stack.append((node[1], (code << 1) | 1, count + 1))
    return table


_CODES = _codes()


def decode(data: bytes, limit: int = 1 << 20) -> bytes:
    """Decompress a packet (or strip the marker of an uncompressed one)."""
    if not data:
        return b""
    if data[0] == UNCOMPRESSED:
        return bytes(data[1:])
    padding = data[0]
    total = (len(data) - 1) * 8 - padding
    out = bytearray()
    node = _ROOT
    used = 0
    for byte in data[1:]:
        for shift in range(8):  # least significant bit first: see the module docstring
            if used >= total:
                return bytes(out)
            node = node[(byte >> shift) & 1]
            used += 1
            if isinstance(node, int):
                out.append(node)
                if len(out) >= limit:
                    return bytes(out)
                node = _ROOT
    return bytes(out)


def encode(data: bytes) -> bytes:
    """Compress data. Like the original, falls back to the 0xFF form when compressing would
    make it longer."""
    bits = 0
    count = 0
    packed = bytearray()
    for value in data:
        code, length = _CODES[value]
        for position in range(length - 1, -1, -1):  # code bits, first bit first
            bits |= ((code >> position) & 1) << count  # stored least significant bit first
            count += 1
            if count == 8:
                packed.append(bits)
                bits = count = 0
    padding = 0
    if count:
        packed.append(bits)
        padding = 8 - count
    result = bytes([padding]) + bytes(packed)
    if len(result) > len(data) + 1:
        return bytes([UNCOMPRESSED]) + bytes(data)
    return result
