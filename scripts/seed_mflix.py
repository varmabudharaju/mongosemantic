"""Fetch MongoDB's official ``sample_mflix`` movies dataset and load it
into the configured demo database.

Why mflix:

- ~23,500 movies, each with title, plot summary, full plot, genres, cast,
  year, directors, IMDb / Rotten Tomatoes ratings.
- Real text (not lorem-ipsum), naturally diverse, naturally clusters by
  genre on the visualize page.
- It's MongoDB's own sample dataset — the same one Atlas seeds. On-brand.

Run::

    MONGOSEMANTIC_URI="mongodb://localhost:27117/?replicaSet=rs0" \\
    MONGOSEMANTIC_DB=demo \\
        python3 scripts/seed_mflix.py

By default this **augments** the database — it drops the ``movies`` collection
only. Pass ``--wipe`` to also drop any prior mongosemantic config / jobs /
shadow collections (handy after switching between datasets).
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

from bson import json_util

from mongosemantic.db.client import MongoConnection, redact_uri, scrub_uri

# Community mirror of MongoDB's `sample_mflix`. The same JSON Atlas seeds.
SOURCE_URL = (
    "https://raw.githubusercontent.com/"
    "neelabalan/mongodb-sample-dataset/main/sample_mflix/movies.json"
)

URI = os.environ.get("MONGOSEMANTIC_URI", "mongodb://localhost:27117/?replicaSet=rs0")
DB_NAME = os.environ.get("MONGOSEMANTIC_DB", "demo")


def _download(url: str, dst: Path, timeout: int = 60) -> None:
    """Download `url` to `dst`. Prints a one-line progress at the end."""
    print(f"fetching {url} ...")
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        total = resp.length or 0
        size_mb = total / (1024 * 1024) if total else None
        if size_mb:
            print(f"  size: {size_mb:.1f} MB")
        with open(dst, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
    print(f"  saved to {dst}")


def _stream_docs(path: Path):
    """Yield parsed Mongo extended-JSON docs from a JSONL file."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json_util.loads(line)


def _maybe_wipe(db, wipe: bool) -> None:
    db.drop_collection("movies")
    if wipe:
        for name in db.list_collection_names():
            if (
                name == "movies_embeddings"
                or name.startswith("movies_embeddings_")
                or name.startswith("mongosemantic_")
            ):
                db.drop_collection(name)


def _bulk_insert(db, docs_iter, batch_size: int = 1000) -> int:
    coll = db["movies"]
    batch: list[dict] = []
    total = 0
    for doc in docs_iter:
        batch.append(doc)
        if len(batch) >= batch_size:
            coll.insert_many(batch, ordered=False)
            total += len(batch)
            print(f"  inserted {total:>6} …", end="\r", flush=True)
            batch.clear()
    if batch:
        coll.insert_many(batch, ordered=False)
        total += len(batch)
    print(f"  inserted {total:>6} movies      ")
    return total


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--wipe", action="store_true",
        help="Also drop mongosemantic config / jobs / movies_embeddings.",
    )
    p.add_argument(
        "--from-file", type=Path, default=None,
        help="Skip the download and read from a local JSONL file instead.",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="Insert at most N movies. Useful for tests.",
    )
    args = p.parse_args(argv)

    if args.from_file:
        source = args.from_file
        if not source.exists():
            print(f"file not found: {source}", file=sys.stderr)
            return 2
    else:
        tmp = Path(tempfile.gettempdir()) / "mongosemantic_mflix.json"
        try:
            _download(SOURCE_URL, tmp)
        except Exception as e:
            print(f"download failed: {e}", file=sys.stderr)
            print(
                "  Try again or use --from-file with a pre-downloaded JSONL.",
                file=sys.stderr,
            )
            return 3
        source = tmp

    # MongoConnection.open gets us the certifi-backed TLS config the CLI
    # uses; a bare MongoClient fails cert verification on macOS Pythons
    # without a system CA bundle.
    try:
        conn = MongoConnection.open(URI, DB_NAME)
    except Exception as e:
        print(f"could not reach {redact_uri(URI)}: {scrub_uri(str(e), URI)}", file=sys.stderr)
        return 2
    client = conn.client
    db = conn.db

    print(f"writing to {DB_NAME}@{redact_uri(URI)}")
    _maybe_wipe(db, args.wipe)

    started = datetime.utcnow()

    def _iter():
        for i, doc in enumerate(_stream_docs(source)):
            if args.limit is not None and i >= args.limit:
                break
            yield doc

    inserted = _bulk_insert(db, _iter())
    elapsed = (datetime.utcnow() - started).total_seconds()
    print(f"done in {elapsed:.1f}s — {inserted} movies inserted")
    if inserted:
        sample = db["movies"].find_one({"plot": {"$exists": True}})
        if sample:
            print(
                f"sample: {sample.get('title')!r} ({sample.get('year')}) "
                f"genres={sample.get('genres')}"
            )
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
