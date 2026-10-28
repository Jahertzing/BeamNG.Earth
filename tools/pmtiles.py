"""A minimal PMTiles v3 reader over HTTP byte ranges (https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md),
for tools/preload.py: which byte ranges of an archive hold a set of tiles, so a run of neighbouring tiles comes in one
range request instead of one request each. Tiles are stored by Hilbert tile id, so a region's tiles at one zoom lie
in a few long runs. Pure but for the read callback: read(offset, length) -> bytes.
"""
import gzip
import struct

HEADER_LEN = 127
FIRST_READ = 16384              # the header and (the spec says) the root directory fit in the first 16 KiB


class PMTilesError(ValueError):
    pass


# --- tile ids (Hilbert curve per zoom, zooms in order) ---------------------------------------------------------------
def zxy_to_id(z, x, y):
    acc = ((1 << (2 * z)) - 1) // 3          # tiles of every zoom before z: sum of 4^i
    n = 1 << z
    d = 0
    s = n >> 1
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:                         # rotate
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        s >>= 1
    return acc + d


def id_to_zxy(tid):
    z = 0
    acc = 0
    while True:
        num = 1 << (2 * z)
        if acc + num > tid:
            break
        acc += num
        z += 1
    d = tid - acc
    n = 1 << z
    x = y = 0
    s = 1
    while s < n:
        rx = 1 & (d >> 1)
        ry = 1 & (d ^ rx)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        x += s * rx
        y += s * ry
        d >>= 2
        s <<= 1
    return z, x, y


# --- header and directories ------------------------------------------------------------------------------------------
def _varint(b, i):
    v = shift = 0
    while True:
        c = b[i]
        i += 1
        v |= (c & 0x7F) << shift
        if c < 0x80:
            return v, i
        shift += 7


def parse_header(b):
    if len(b) < HEADER_LEN or b[:7] != b"PMTiles" or b[7] != 3:
        raise PMTilesError("not a PMTiles v3 archive")
    (root_off, root_len, meta_off, meta_len, leaf_off, leaf_len, data_off, data_len, n_addr, n_entries,
     n_contents) = struct.unpack_from("<11Q", b, 8)
    clustered, icomp, tcomp, ttype, minz, maxz = struct.unpack_from("<6B", b, 96)
    return dict(root_off=root_off, root_len=root_len, leaf_off=leaf_off, leaf_len=leaf_len, data_off=data_off,
                data_len=data_len, clustered=bool(clustered), internal_compression=icomp, tile_compression=tcomp,
                tile_type=ttype, min_zoom=minz, max_zoom=maxz, addressed=n_addr)


def parse_directory(b, compression):
    """[(tile_id, offset, length, run_length)] of a (decompressed) directory; run_length 0: a leaf directory."""
    if compression == 2:
        b = gzip.decompress(b)
    elif compression not in (0, 1):
        raise PMTilesError(f"internal compression {compression} not supported")
    n, i = _varint(b, 0)
    ids, runs, lens, offs = [0] * n, [0] * n, [0] * n, [0] * n
    last = 0
    for k in range(n):
        d, i = _varint(b, i)
        last += d
        ids[k] = last
    for k in range(n):
        runs[k], i = _varint(b, i)
    for k in range(n):
        lens[k], i = _varint(b, i)
    for k in range(n):
        v, i = _varint(b, i)
        offs[k] = offs[k - 1] + lens[k - 1] if (v == 0 and k > 0) else v - 1
    return list(zip(ids, offs, lens, runs))


class Archive:
    """An archive read through read(offset, length): find(tile ids) -> {tile id: (offset, length)} in the tile data
    section's absolute offsets, the leaf directories it needs read once each (kept)."""

    def __init__(self, read):
        self.read = read
        first = read(0, FIRST_READ)
        self.header = h = parse_header(first)
        if h["root_off"] + h["root_len"] <= len(first):
            root = first[h["root_off"]:h["root_off"] + h["root_len"]]
        else:
            root = read(h["root_off"], h["root_len"])
        self.root = parse_directory(root, h["internal_compression"])
        self._leaves = {}
        self.reads = 1 + (h["root_off"] + h["root_len"] > len(first))

    def _leaf(self, off, length):
        key = (off, length)
        if key not in self._leaves:
            self._leaves[key] = parse_directory(self.read(self.header["leaf_off"] + off, length),
                                                self.header["internal_compression"])
            self.reads += 1
        return self._leaves[key]

    @staticmethod
    def _lookup(entries, tid):
        """The entry holding tid (binary search), or None."""
        lo, hi = 0, len(entries) - 1
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if entries[mid][0] <= tid:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best is None:
            return None
        e = entries[best]
        if e[3] == 0 or tid < e[0] + e[3]:       # a leaf (searched below), or inside the entry's run
            return e
        return None

    def find(self, tids):
        out = {}
        data = self.header["data_off"]
        for tid in sorted(set(tids)):
            entries = self.root
            for _ in range(4):                  # root -> leaves (the spec allows a few levels)
                e = self._lookup(entries, tid)
                if e is None:
                    break
                if e[3] == 0:
                    entries = self._leaf(e[1], e[2])
                    continue
                out[tid] = (data + e[1], e[2])
                break
        return out


def plan_ranges(spans, gap=256 * 1024, max_len=8 * 1024 * 1024):
    """[(start, end, [keys])] covering {key: (offset, length)}: neighbours closer than gap bytes in one range, at most
    max_len bytes a range (one tile larger than that alone). Pure."""
    items = sorted(spans.items(), key=lambda kv: kv[1][0])
    out = []
    for key, (off, ln) in items:
        if out and off - out[-1][1] <= gap and off + ln - out[-1][0] <= max_len:
            s, e, keys = out[-1]
            out[-1] = (s, max(e, off + ln), keys + [key])
        else:
            out.append((off, off + ln, [key]))
    return out
