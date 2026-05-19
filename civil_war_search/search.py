from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory

from .manifest import ArchiveRecord, read_manifest
from .matcher import build_matcher, load_keywords, normalize_text
from .paths import PageIdentity, parse_ocr_member_path


@dataclass(frozen=True, slots=True)
class SearchStats:
    archive: str
    pages_seen: int = 0
    pages_in_range: int = 0
    matched_pages: int = 0


def _json_record(
    page: PageIdentity,
    archive: ArchiveRecord,
    matches: dict[str, list[int]],
    normalized_text: str,
    snippet_radius: int,
) -> dict[str, object]:
    snippets: dict[str, list[str]] = {}
    if snippet_radius > 0:
        for keyword, positions in matches.items():
            snippets[keyword] = []
            for position in positions[:3]:
                start = max(0, position - snippet_radius)
                end = min(len(normalized_text), position + len(keyword) + snippet_radius)
                snippets[keyword].append(normalized_text[start:end].strip())

    return {
        "lccn": page.lccn,
        "date": page.date_text,
        "edition": page.edition,
        "sequence": page.sequence,
        "page_url": page.page_url,
        "archive": archive.filename,
        "archive_url": archive.url,
        "matched_keywords": sorted(matches),
        "keyword_match_counts": {
            keyword: len(positions) for keyword, positions in sorted(matches.items())
        },
        "match_count": sum(len(positions) for positions in matches.values()),
        "snippets": snippets,
    }


def search_archive(
    record: ArchiveRecord,
    keywords: list[str],
    start_date: date,
    end_date: date,
    output_path: str,
    snippet_radius: int,
) -> SearchStats:
    archive_path = Path(record.local_path or record.filename)
    matcher = build_matcher(keywords)
    pages_seen = 0
    pages_in_range = 0
    matched_pages = 0

    with tarfile.open(archive_path, mode="r|bz2") as archive, open(
        output_path, "w", encoding="utf-8"
    ) as output:
        for member in archive:
            if not member.isfile() or not member.name.endswith("/ocr.txt"):
                continue
            pages_seen += 1
            page = parse_ocr_member_path(member.name)
            if page is None or page.date < start_date or page.date > end_date:
                continue
            pages_in_range += 1

            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            text = extracted.read().decode("utf-8", errors="replace")
            normalized = normalize_text(text)
            matches = matcher.find(normalized)
            if not matches:
                continue

            matched_pages += 1
            output.write(
                json.dumps(
                    _json_record(page, record, matches, normalized, snippet_radius),
                    sort_keys=True,
                )
                + "\n"
            )

    return SearchStats(
        archive=record.filename,
        pages_seen=pages_seen,
        pages_in_range=pages_in_range,
        matched_pages=matched_pages,
    )


def _state_path_for(output_path: str, state_path: str | None) -> Path:
    if state_path:
        return Path(state_path)
    output = Path(output_path)
    return output.with_suffix(output.suffix + ".state.json")


def _read_completed(state_path: Path) -> set[str]:
    if not state_path.exists():
        return set()
    with state_path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return set(data.get("completed_archives", []))


def _write_completed(state_path: Path, completed: set[str]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("w", encoding="utf-8") as handle:
        json.dump({"completed_archives": sorted(completed)}, handle, indent=2)


def _archive_exists(record: ArchiveRecord) -> bool:
    if record.local_path:
        return Path(record.local_path).exists()
    return Path(record.filename).exists()


def search_manifest(
    manifest_path: str,
    keywords_path: str,
    output_path: str,
    workers: int,
    start_date: date = date(1860, 1, 1),
    end_date: date = date(1865, 12, 31),
    state_path: str | None = None,
    snippet_radius: int = 80,
) -> list[SearchStats]:
    records = read_manifest(manifest_path)
    keywords = load_keywords(keywords_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    state = _state_path_for(output_path, state_path)
    completed = _read_completed(state)

    searchable = [
        record
        for record in records
        if record.filename not in completed and _archive_exists(record)
    ]
    stats: list[SearchStats] = []

    with TemporaryDirectory(prefix="civil-war-search-") as temp_dir:
        temp = Path(temp_dir)
        with ProcessPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {}
            for index, record in enumerate(searchable):
                part_path = temp / f"{index:06d}-{record.filename}.jsonl"
                futures[
                    executor.submit(
                        search_archive,
                        record,
                        keywords,
                        start_date,
                        end_date,
                        str(part_path),
                        snippet_radius,
                    )
                ] = (record, part_path)

            with output.open("a", encoding="utf-8") as merged:
                for future in as_completed(futures):
                    record, part_path = futures[future]
                    archive_stats = future.result()
                    stats.append(archive_stats)
                    if part_path.exists():
                        with part_path.open(encoding="utf-8") as part:
                            for line in part:
                                merged.write(line)
                    completed.add(record.filename)
                    _write_completed(state, completed)

    return stats


def stats_as_dicts(stats: list[SearchStats]) -> list[dict[str, object]]:
    return [asdict(item) for item in stats]
