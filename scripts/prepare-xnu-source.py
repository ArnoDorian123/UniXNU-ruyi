#!/usr/bin/env python3
"""Restore omitted DriverKit headers without touching api.github.com.

ruyi override of UniXNU/scripts/prepare-xnu-source.py.

Why this exists
---------------
The upstream script reads a directory listing from
https://api.github.com/repos/apple-oss-distributions/xnu/contents/... .
Unauthenticated api.github.com allows 60 requests per hour per IP, and the egress IP of a
hosted GitHub runner is shared with every other job on that machine. In practice the step
fails with "HTTP Error 403: rate limit exceeded" and the whole kernel build stops before it
starts.

This version downloads the tagged source tarball from codeload.github.com instead. That host
serves release archives and does not apply the REST API quota. The extracted headers, the
destination paths and the manifest are the same as upstream.

The device repository overrides a base repository module with the same name, so once this
file is committed as scripts/prepare-xnu-source.py the driver uses it instead of the
upstream one.
"""

import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import time
import urllib.error
import urllib.request

# Same defaults as upstream (feeds.toml may override them through the environment).
TAG = os.environ.get("UNIXNU_DRIVERKIT_TAG", "xnu-10063.121.3")
TRUST_TAG = os.environ.get("UNIXNU_TRUSTCACHE_TAG", "xnu-8796.101.5")

WORK = Path(".cache/xnu-work")
SOURCES = Path(".cache/sources")
SOURCES.mkdir(parents=True, exist_ok=True)

TARBALL = "https://codeload.github.com/apple-oss-distributions/xnu/tar.gz/refs/tags/{tag}"

# prefix inside the archive -> destination directory inside .cache/xnu-work
TARGETS = [
    ("iokit/DriverKit", WORK / "iokit" / "DriverKit", TAG),
    ("EXTERNAL_HEADERS/TrustCache", WORK / "EXTERNAL_HEADERS" / "TrustCache", TRUST_TAG),
]


def download(url, attempts=3):
    last = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=300) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            last = error
            time.sleep(5 * (attempt + 1))
    raise SystemExit(f"download failed after {attempts} attempts: {url}: {last}")


def archive(tag):
    data = download(TARBALL.format(tag=tag))
    return tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")


def git_blob(data):
    header = b"blob " + str(len(data)).encode() + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def restore(tar, prefix, destination, tag):
    destination.mkdir(parents=True, exist_ok=True)
    restored = []
    for member in tar.getmembers():
        if not member.isfile() or not member.name.endswith(".h"):
            continue
        parts = member.name.split("/", 1)
        if len(parts) != 2 or not parts[1].startswith(prefix + "/"):
            continue
        relative = parts[1][len(prefix) + 1:]
        target = destination / relative
        if target.exists():
            continue
        data = tar.extractfile(member).read()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        restored.append({
            "name": relative,
            "source_tag": tag,
            "git_blob": git_blob(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    return restored


def main():
    restored = []
    current = None
    opened = None
    for prefix, destination, tag in TARGETS:
        if tag != current:
            opened = archive(tag)
            current = tag
        found = restore(opened, prefix, destination, tag)
        print(f"{prefix}: restored {len(found)} headers from {tag}")
        restored.extend(found)

    if not restored:
        print("nothing to restore (headers already present)")
        return

    previous = SOURCES / "xnu-restored-headers.json"
    existing = json.loads(previous.read_text()) if previous.exists() else []
    by_name = {entry["name"]: entry for entry in existing + restored}
    previous.write_text(json.dumps(list(by_name.values()), indent=2) + "\n")
    print(f"Restored {len(restored)} omitted public headers from Apple; hashes recorded")


if __name__ == "__main__":
    main()
