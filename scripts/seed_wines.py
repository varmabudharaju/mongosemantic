"""Fetch the Wine Reviews dataset (130k reviews) and load it into the
configured demo database.

Why wine reviews:

- ~130,000 reviews, each with country, province, variety, winery, price,
  points, designation, taster — plus a rich sensory description like
  "Aromas of black cherry, tobacco, and leather; medium-bodied with firm
  tannins."
- The description text is exactly the kind of subjective prose where
  semantic search beats keyword search by a mile: "elegant red for
  steak", "crisp white under $20", "bold cab with smoke" all work.
- Sourced from the TidyTuesday GitHub mirror — no Kaggle login needed.

Run::

    MONGOSEMANTIC_URI="mongodb://localhost:27117/?replicaSet=rs0" \\
    MONGOSEMANTIC_DB=demo \\
        python3 scripts/seed_wines.py

By default this **augments** the database — it drops the ``wines`` collection
only. Pass ``--wipe`` to also drop any prior mongosemantic config / jobs /
shadow collections (handy after switching between datasets).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

from mongosemantic.db.client import MongoConnection, redact_uri, scrub_uri

SOURCE_URL = (
    "https://raw.githubusercontent.com/rfordatascience/tidytuesday/"
    "master/data/2019/2019-05-28/winemag-data-130k-v2.csv"
)

URI = os.environ.get("MONGOSEMANTIC_URI", "mongodb://localhost:27117/?replicaSet=rs0")
DB_NAME = os.environ.get("MONGOSEMANTIC_DB", "demo")


def _download(url: str, dst: Path, timeout: int = 60) -> None:
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
    """Yield wine reviews from the CSV.

    The TidyTuesday CSV has a leading unnamed index column we drop, and
    `price`/`points` come in as strings — coerce to numbers so range
    queries work without surprises.
    """
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row.pop("", None)  # unnamed index column from pandas export
            for k in ("price", "points"):
                v = row.get(k)
                if v in (None, ""):
                    row[k] = None
                else:
                    try:
                        row[k] = float(v) if k == "price" else int(v)
                    except ValueError:
                        row[k] = None
            yield row


def _maybe_wipe(db, wipe: bool) -> None:
    db.drop_collection("wines")
    if wipe:
        for name in db.list_collection_names():
            if (
                name == "wines_embeddings"
                or name.startswith("wines_embeddings_")
                or name.startswith("mongosemantic_")
            ):
                db.drop_collection(name)


def _bulk_insert(db, docs_iter, batch_size: int = 1000) -> int:
    coll = db["wines"]
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
    print(f"  inserted {total:>6} wines      ")
    return total


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--wipe", action="store_true",
        help="Also drop mongosemantic config / jobs / wines_embeddings.",
    )
    p.add_argument(
        "--from-file", type=Path, default=None,
        help="Skip the download and read from a local CSV file instead.",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="Insert at most N wines. Useful for tests.",
    )
    args = p.parse_args(argv)

    if args.from_file:
        source = args.from_file
        if not source.exists():
            print(f"file not found: {source}", file=sys.stderr)
            return 2
    else:
        tmp = Path(tempfile.gettempdir()) / "mongosemantic_wines.csv"
        try:
            _download(SOURCE_URL, tmp)
        except Exception as e:
            print(f"download failed: {e}", file=sys.stderr)
            print(
                "  Try again or use --from-file with a pre-downloaded CSV.",
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
    print(f"done in {elapsed:.1f}s — {inserted} wines inserted")
    if inserted:
        sample = db["wines"].find_one({"description": {"$exists": True}})
        if sample:
            desc = (sample.get("description") or "")[:80]
            print(
                f"sample: {sample.get('title')!r} "
                f"({sample.get('variety')}, {sample.get('country')}) — {desc}…"
            )
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
