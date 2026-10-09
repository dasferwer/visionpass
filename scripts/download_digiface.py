"""Выборка официального ZIP через HTTP Range; изображения остаются вне Git."""

import argparse
import hashlib
import io
import json
import struct
import urllib.request
import zipfile
import zlib
from pathlib import Path

SOURCE = (
    "https://facesyntheticspubwedata.z6.web.core.windows.net/wacv-2023/subjects_0-1999_72_imgs.zip"
)
ETAG = '"0x8DD0F91A114C3C7"'
SIZE = 2938553163
IDENTITIES = tuple(range(12))
RENDERS = (0, 1, 18)


def fetch(start: int, end: int) -> bytes:
    request = urllib.request.Request(
        SOURCE, headers={"Range": f"bytes={start}-{end}", "If-Match": ETAG}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        if response.status != 206 or response.headers.get("ETag") != ETAG:
            raise RuntimeError("Source changed or server ignored bounded Range")
        data = response.read(end - start + 2)
    if len(data) != end - start + 1:
        raise RuntimeError("Incomplete/oversized Range")
    return data


def download(destination: Path, manifest_path: Path) -> None:
    # ZIP central directory, not the 2.94 GB complete archive.
    tail = fetch(SIZE - 65557, SIZE - 1)
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0:
        raise RuntimeError("Missing ZIP directory")
    record = struct.unpack_from("<4s4H2LH", tail, offset)
    directory_size, directory_offset = record[5:7]
    if directory_size > 32 * 1024 * 1024:
        raise RuntimeError("ZIP directory exceeds budget")
    directory = fetch(directory_offset, directory_offset + directory_size - 1)
    # Rebase local offsets only while reading directory metadata; no extraction paths trusted.
    archive = zipfile.ZipFile(io.BytesIO(directory + tail[offset:]))
    entries = archive.infolist()
    selected = []
    for identity in IDENTITIES:
        for render in RENDERS:
            suffix = f"{identity}/{render}.png"
            candidates = [e for e in entries if e.filename == suffix]
            if len(candidates) != 1:
                raise RuntimeError(f"Expected one entry for {suffix}: {len(candidates)}")
            entry = candidates[0]
            if entry.file_size > 5 * 1024 * 1024:
                raise RuntimeError("Image exceeds budget")
            local_offset = entry.header_offset + directory_offset
            header = fetch(local_offset, local_offset + 29)
            fields = struct.unpack("<4s5H3L2H", header)
            start = local_offset + 30 + fields[-2] + fields[-1]
            if entry.compress_size > 5 * 1024 * 1024:
                raise RuntimeError("Compressed image exceeds budget")
            compressed = fetch(start, start + entry.compress_size - 1)
            if entry.compress_type == zipfile.ZIP_STORED:
                data = compressed
            elif entry.compress_type == zipfile.ZIP_DEFLATED:
                decompressor = zlib.decompressobj(-15)
                data = decompressor.decompress(compressed, entry.file_size + 1)
                if not decompressor.eof:
                    raise RuntimeError("Incomplete deflate stream")
            else:
                raise RuntimeError("Unexpected compression")
            if len(data) != entry.file_size or zlib.crc32(data) != entry.CRC:
                raise RuntimeError("ZIP size/CRC mismatch")
            relative = f"{identity}/{render}.png"
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            selected.append(
                {
                    "identity": str(identity),
                    "render": render,
                    "path": relative,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                }
            )
    manifest = {
        "dataset": "Microsoft DigiFace-1M",
        "source_repository_revision": "1d173f305de9afefafb8589ce8e726ec685863e5",
        "source": SOURCE,
        "etag": ETAG,
        "archive_bytes": SIZE,
        "directory_sha256": hashlib.sha256(directory).hexdigest(),
        "license": "R-UDA v1.0; non-commercial research only",
        "selection": "identities 0..11; renders 0,1,18; chosen before CV execution",
        "split": "no training/calibration; all 12 held-out synthetic identities",
        "images": selected,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    download(args.destination, args.manifest)
