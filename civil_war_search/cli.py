from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
import os

from .download import download_archives
from .manifest import OCR_INDEX_URL, build_manifest, split_manifest
from .process import process_manifest
from .search import search_manifest, stats_as_dicts


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date: {value}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="civil-war-search",
        description="Search Chronicling America bulk OCR archives.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest", help="build an OCR archive manifest")
    manifest.add_argument("--out", required=True, help="manifest JSONL path")
    manifest.add_argument("--source-url", default=OCR_INDEX_URL)

    chunk = subparsers.add_parser(
        "chunk-manifest", help="split a manifest into smaller JSONL manifests"
    )
    chunk.add_argument("--manifest", required=True)
    chunk.add_argument("--dir", required=True, help="chunk output directory")
    chunk.add_argument("--chunk-size", type=int, default=100)

    download = subparsers.add_parser("download", help="download OCR archives")
    download.add_argument("--manifest", required=True)
    download.add_argument("--dir", required=True, help="archive output directory")
    download.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    download.add_argument("--no-verify", action="store_true")

    search = subparsers.add_parser("search", help="search downloaded OCR archives")
    search.add_argument("--manifest", required=True)
    search.add_argument("--keywords", required=True)
    search.add_argument("--out", required=True)
    search.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    search.add_argument("--start-date", type=_parse_date, default=date(1860, 1, 1))
    search.add_argument("--end-date", type=_parse_date, default=date(1865, 12, 31))
    search.add_argument("--state")
    search.add_argument("--snippet-radius", type=int, default=80)

    process = subparsers.add_parser(
        "process",
        help="download, search, and delete archives one at a time",
    )
    process.add_argument("--manifest", required=True)
    process.add_argument("--keywords", required=True)
    process.add_argument("--out", required=True)
    process.add_argument("--cache-dir", default="data/archive-cache")
    process.add_argument("--start-date", type=_parse_date, default=date(1860, 1, 1))
    process.add_argument("--end-date", type=_parse_date, default=date(1865, 12, 31))
    process.add_argument("--state")
    process.add_argument("--parts-dir")
    process.add_argument("--snippet-radius", type=int, default=80)
    process.add_argument("--retries", type=int, default=2)
    process.add_argument("--retry-sleep", type=float, default=5.0)
    process.add_argument("--no-verify", action="store_true")
    process.add_argument("--keep-archives", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.command == "manifest":
        records = build_manifest(args.out, args.source_url)
        print(f"wrote {len(records)} archive records to {args.out}")
        return

    if args.command == "chunk-manifest":
        paths = split_manifest(args.manifest, args.dir, args.chunk_size)
        print(f"wrote {len(paths)} manifest chunks to {args.dir}")
        return

    if args.command == "download":
        records = download_archives(
            args.manifest,
            args.dir,
            args.workers,
            verify=not args.no_verify,
        )
        downloaded = sum(record.status == "downloaded" for record in records)
        print(f"downloaded {downloaded}/{len(records)} archives")
        return

    if args.command == "search":
        stats = search_manifest(
            args.manifest,
            args.keywords,
            args.out,
            args.workers,
            start_date=args.start_date,
            end_date=args.end_date,
            state_path=args.state,
            snippet_radius=args.snippet_radius,
        )
        print(json.dumps(stats_as_dicts(stats), indent=2))
        return

    if args.command == "process":
        summary = process_manifest(
            args.manifest,
            args.keywords,
            args.out,
            args.cache_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            state_path=args.state,
            parts_dir=args.parts_dir,
            snippet_radius=args.snippet_radius,
            verify=not args.no_verify,
            retries=args.retries,
            retry_sleep=args.retry_sleep,
            keep_archives=args.keep_archives,
        )
        print(json.dumps(asdict(summary), indent=2))
        if summary.failed:
            raise SystemExit(1)
        return

    raise AssertionError(f"unhandled command: {args.command}")
