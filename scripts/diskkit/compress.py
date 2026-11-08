"""Big files that would shrink a lot with lossless compression.

We sample a few 1 MB chunks across the file, compress them with zlib level 1 and
extrapolate. zstd usually does as well or better, so the estimate is conservative.
GeoTIFFs get a header check: Compression tag 1 means stored raw.
"""

from __future__ import annotations

import os
import struct
import zlib

from . import rules
from .fsutil import os_path

SAMPLE = 1024 * 1024


def estimate_ratio(path: str, size: int, samples: int = 4) -> float | None:
    if size < SAMPLE:
        return None
    raw = comp = 0
    try:
        with open(os_path(path), "rb") as fh:
            for i in range(samples):
                fh.seek(int((size - SAMPLE) * i / max(samples - 1, 1)))
                buf = fh.read(SAMPLE)
                if not buf:
                    continue
                raw += len(buf)
                comp += len(zlib.compress(buf, 1))
    except OSError:
        return None
    return comp / raw if raw else None


def tiff_compression(path: str) -> int | None:
    """Value of TIFF tag 259 in the first IFD (1 = none, 5 = LZW, 8 = deflate, 50000 = zstd)."""
    try:
        with open(os_path(path), "rb") as fh:
            head = fh.read(16)
            if head[:2] == b"II":
                e = "<"
            elif head[:2] == b"MM":
                e = ">"
            else:
                return None
            magic = struct.unpack(e + "H", head[2:4])[0]
            if magic == 42:
                fh.seek(struct.unpack(e + "I", head[4:8])[0])
                n = struct.unpack(e + "H", fh.read(2))[0]
                for _ in range(n):
                    tag, typ, _cnt, val = struct.unpack(e + "HHI4s", fh.read(12))
                    if tag == 259:
                        return struct.unpack(e + "H", val[:2])[0]
            elif magic == 43:  # BigTIFF
                fh.seek(struct.unpack(e + "Q", head[8:16])[0])
                n = struct.unpack(e + "Q", fh.read(8))[0]
                for _ in range(n):
                    tag, typ, _cnt, val = struct.unpack(e + "HHQ8s", fh.read(20))
                    if tag == 259:
                        return struct.unpack(e + "H", val[:2])[0]
    except (OSError, struct.error):
        return None
    return None


def advice_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".tif", ".tiff"):
        return ("gdal_translate -co COMPRESS=ZSTD -co PREDICTOR=2 -co TILED=YES -co BIGTIFF=IF_SAFER "
                "in.tif out.tif  (lossless; use PREDICTOR=3 for float rasters, or -of COG)")
    if ext in (".csv", ".tsv"):
        return "convert to Parquet (zstd) for analysis, or `zstd -19 --long` / gzip to archive"
    if ext in (".json", ".geojson", ".ndjson", ".jsonl"):
        return "gzip/zstd it, or GeoParquet/FlatGeobuf for vector data"
    if ext in (".log", ".txt", ".out"):
        return "delete if stale, otherwise gzip/zstd"
    if ext in (".npy", ".memmap", ".raw", ".bin", ".dat"):
        return "np.savez_compressed / zarr with zstd, or `zstd --long` to archive"
    if ext in (".sql", ".dump", ".bak"):
        return "zstd -19 --long (database dumps compress 5-20x)"
    if ext in (".img", ".ige", ".bil", ".bsq", ".envi"):
        return "gdal_translate to a zstd-compressed GeoTIFF/COG"
    if ext in (".duckdb", ".db", ".sqlite", ".sqlite3", ".mdb", ".accdb"):
        return ("live database: CHECKPOINT/VACUUM first (or export + re-import); archive copies: "
                "zstd -19 --long")
    if ext in (".shp", ".dbf", ".shx"):
        return "convert the shapefile to GeoParquet or FlatGeobuf (smaller, one file), or zip it"
    if ext in (".las",):
        return "convert to LAZ (laszip / pdal), lossless"
    if ext in (".wav", ".aif", ".aiff"):
        return "FLAC is lossless and about half the size"
    if ext in (".bmp",):
        return "PNG is lossless and much smaller"
    return "zstd it, or NTFS-compress the folder (`compact /c /s /exe:lzx`, NTFS only)"


def find_compressible(files, *, min_ratio_gain: float = 0.4, min_size: int = 100 * 1024**2,
                      max_files: int = 300) -> list[dict]:
    """files: iterable of FileInfo (already filtered to big ones by the scan)."""
    out = []
    checked = 0
    for f in sorted(files, key=lambda x: -x.size):
        if f.size < min_size or f.cloud:
            continue
        stem, ext = rules.split_name(os.path.basename(f.path))
        if ext.lower() in rules.ALREADY_COMPRESSED_EXTS:
            continue
        checked += 1
        if checked > max_files:
            break
        tiff = None
        if ext.lower() in (".tif", ".tiff"):
            tiff = tiff_compression(f.path)
            if tiff not in (None, 1):
                continue  # already compressed TIFF
        ratio = estimate_ratio(f.path, f.size)
        if ratio is None or ratio > 1 - min_ratio_gain:
            continue
        out.append({"path": f.path, "bytes": f.size, "est_ratio": round(ratio, 3),
                    "est_saving": int(f.size * (1 - ratio)), "tiff_uncompressed": tiff == 1,
                    "advice": advice_for(f.path)})
    out.sort(key=lambda d: -d["est_saving"])
    return out
