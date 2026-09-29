#!/usr/bin/env python3
"""Safely stream-extract the local ListenBrainz .tar.zst sample."""

from __future__ import annotations

import argparse
import os
import sys
import tarfile
from pathlib import Path

try:
    import zstandard
except ImportError as error:
    raise SystemExit("install the 'zstandard' Python package before running this extractor") from error


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=root / "downloads" / "listenbrainz-sample-dump-20250610-094311.tar.zst",
    )
    parser.add_argument(
        "--destination", type=Path, default=root / "data" / "listenbrainz-sample"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    archive = args.archive.resolve()
    destination = args.destination.resolve()
    partial = destination.with_name(destination.name + ".partial")
    if destination.exists():
        print(f"already extracted: {destination}")
        return 0
    if partial.exists():
        raise SystemExit(f"partial extraction already exists; inspect it before removing it: {partial}")
    partial.mkdir(parents=True)
    partial_root = partial.resolve()
    files = 0
    bytes_written = 0
    decompressor = zstandard.ZstdDecompressor()
    with archive.open("rb") as compressed:
        with decompressor.stream_reader(compressed) as stream:
            with tarfile.open(fileobj=stream, mode="r|") as tar:
                for member in tar:
                    target = (partial_root / member.name).resolve()
                    if not target.is_relative_to(partial_root):
                        raise RuntimeError(f"unsafe archive path: {member.name}")
                    if member.issym() or member.islnk():
                        raise RuntimeError(f"archive links are not allowed: {member.name}")
                    tar.extract(member, path=partial_root, filter="data")
                    if member.isfile():
                        files += 1
                        bytes_written += member.size
                        if files % 100 == 0:
                            print(f"{files} files, {bytes_written:,} bytes", flush=True)
    os.replace(partial, destination)
    print(f"extracted {files} files ({bytes_written:,} bytes) to {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

