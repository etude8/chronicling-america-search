from __future__ import annotations

from dataclasses import asdict, dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin
from urllib.request import Request, urlopen


OCR_INDEX_URL = "https://chroniclingamerica.loc.gov/data/ocr/"
OCR_JSON_URL = OCR_INDEX_URL
OCR_BASE_URL = OCR_INDEX_URL


@dataclass(slots=True)
class ArchiveRecord:
    url: str
    filename: str
    batch: str | None = None
    created: str | None = None
    size: str | int | None = None
    sha1: str | None = None
    local_path: str | None = None
    status: str = "pending"


def read_url(url: str = OCR_INDEX_URL) -> tuple[str, str]:
    request = Request(
        url,
        headers={
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "User-Agent": "civil-war-search/0.1",
        },
    )
    with urlopen(request, timeout=60) as response:
        return response.headers.get_content_type(), response.read().decode("utf-8")


def read_json_url(url: str = OCR_INDEX_URL) -> Any:
    content_type, text = read_url(url)
    if content_type != "application/json":
        raise ValueError(f"expected JSON from {url}, got {content_type}")
    return json.loads(text)


class _ArchiveIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, Any]] = []
        self._in_row = False
        self._in_cell = False
        self._current_cells: list[str] = []
        self._current_href: str | None = None
        self._row_href: str | None = None
        self._cell_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._in_row = True
            self._current_cells = []
            self._row_href = None
        elif self._in_row and tag == "td":
            self._in_cell = True
            self._cell_text = []
        elif self._in_cell and tag == "a":
            attrs_dict = dict(attrs)
            self._current_href = attrs_dict.get("href")

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_href:
            self._row_href = self._current_href
            self._current_href = None
        elif tag == "td" and self._in_cell:
            self._current_cells.append(" ".join("".join(self._cell_text).split()))
            self._in_cell = False
            self._cell_text = []
        elif tag == "tr" and self._in_row:
            if self._row_href:
                self.rows.append({"href": self._row_href, "cells": self._current_cells})
            self._in_row = False


def records_from_html(html: str, base_url: str = OCR_BASE_URL) -> list[ArchiveRecord]:
    parser = _ArchiveIndexParser()
    parser.feed(html)

    records: list[ArchiveRecord] = []
    for row in parser.rows:
        href = str(row["href"])
        filename = Path(href.rstrip("/")).name
        if not filename.endswith(".tar.bz2"):
            continue

        cells = list(row.get("cells", []))
        records.append(
            ArchiveRecord(
                url=urljoin(base_url, href),
                filename=filename,
                batch=filename.removesuffix(".tar.bz2"),
                created=cells[1] if len(cells) > 1 else None,
                size=cells[2] if len(cells) > 2 else None,
                sha1=cells[4] if len(cells) > 4 else None,
            )
        )
    return records


def _items_from_payload(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        yield from (item for item in payload if isinstance(item, dict))
        return

    if not isinstance(payload, dict):
        return

    for key in ("items", "resources", "files", "results", "objects"):
        value = payload.get(key)
        if isinstance(value, list):
            yield from (item for item in value if isinstance(item, dict))
            return

    for key, value in payload.items():
        if isinstance(value, dict):
            item = dict(value)
            item.setdefault("filename", key)
            yield item


def _first(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def records_from_payload(payload: Any) -> list[ArchiveRecord]:
    records: list[ArchiveRecord] = []
    for item in _items_from_payload(payload):
        raw_url = _first(item, "url", "link", "href", "download_url", "file")
        filename = _first(item, "filename", "name", "title")
        if raw_url is None and filename is None:
            continue

        url = str(raw_url or filename)
        if not url.startswith(("http://", "https://")):
            url = urljoin(OCR_BASE_URL, url)

        filename = str(filename or Path(url).name)
        if not filename.endswith(".tar.bz2"):
            continue

        records.append(
            ArchiveRecord(
                url=url,
                filename=filename,
                batch=_first(item, "batch", "batch_name", "name"),
                created=_first(item, "created", "date", "created_at"),
                size=_first(item, "size", "bytes", "length"),
                sha1=_first(item, "sha1", "sha1_checksum", "checksum"),
                local_path=_first(item, "local_path", "path"),
                status=str(_first(item, "status") or "pending"),
            )
        )
    return records


def write_manifest(records: Iterable[ArchiveRecord], path: str) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")


def read_manifest(path: str) -> list[ArchiveRecord]:
    records: list[ArchiveRecord] = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                item = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on manifest line {line_number}") from exc
            records.append(ArchiveRecord(**item))
    return records


def split_manifest(manifest_path: str, output_dir: str, chunk_size: int) -> list[Path]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")

    records = read_manifest(manifest_path)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for index in range(0, len(records), chunk_size):
        chunk_number = len(paths) + 1
        chunk_path = directory / f"manifest-{chunk_number:04d}.jsonl"
        write_manifest(records[index : index + chunk_size], str(chunk_path))
        paths.append(chunk_path)

    return paths


def records_from_source(source_url: str = OCR_INDEX_URL) -> list[ArchiveRecord]:
    content_type, text = read_url(source_url)
    if content_type == "application/json" or source_url.endswith(".json"):
        records = records_from_payload(json.loads(text))
    else:
        records = records_from_html(text, source_url)
    return records


def build_manifest(out_path: str, source_url: str = OCR_INDEX_URL) -> list[ArchiveRecord]:
    records = records_from_source(source_url)
    if not records:
        raise ValueError(f"no archive records found in {source_url}")
    write_manifest(records, out_path)
    return records
