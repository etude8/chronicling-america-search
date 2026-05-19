from __future__ import annotations

import csv
from collections import OrderedDict
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import TextIO


SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class IndexSummary:
    keywords: int
    page_keyword_rows: int
    output_dir: str
    summary_path: str


def keyword_slug(keyword: str) -> str:
    slug = SLUG_RE.sub("-", keyword.casefold()).strip("-")
    return slug or "keyword"


def _unique_keyword_path(output_dir: Path, keyword: str, used: dict[str, str]) -> Path:
    base = keyword_slug(keyword)
    filename = f"{base}.jsonl"
    if keyword in used:
        return output_dir / used[keyword]

    if filename in used.values():
        counter = 2
        while f"{base}-{counter}.jsonl" in used.values():
            counter += 1
        filename = f"{base}-{counter}.jsonl"

    used[keyword] = filename
    return output_dir / filename


def index_results(
    results_path: str,
    output_dir: str,
    max_open_files: int = 128,
) -> IndexSummary:
    if max_open_files < 1:
        raise ValueError("max_open_files must be at least 1")

    results = Path(results_path)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    summary_path = directory / "keyword-summary.csv"
    temp_summary_path = summary_path.with_suffix(summary_path.suffix + ".part")
    handles: OrderedDict[str, TextIO] = OrderedDict()
    filenames: dict[str, str] = {}
    initialized: set[str] = set()
    page_counts: dict[str, int] = {}
    occurrence_counts: dict[str, int] = {}
    page_keyword_rows = 0

    def get_handle(keyword: str) -> TextIO:
        handle = handles.get(keyword)
        if handle is not None:
            handles.move_to_end(keyword)
            return handle

        path = _unique_keyword_path(directory, keyword, filenames)
        mode = "a" if keyword in initialized else "w"
        handle = path.open(mode, encoding="utf-8")
        initialized.add(keyword)
        handles[keyword] = handle

        while len(handles) > max_open_files:
            _, stale_handle = handles.popitem(last=False)
            stale_handle.close()

        return handle

    try:
        with results.open(encoding="utf-8") as input_file:
            for line_number, line in enumerate(input_file, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid JSON on results line {line_number}"
                    ) from exc

                matched_keywords = row.get("matched_keywords", [])
                if not isinstance(matched_keywords, list):
                    raise ValueError(
                        f"results line {line_number} has invalid matched_keywords"
                    )
                snippets = row.get("snippets", {})
                if not isinstance(snippets, dict):
                    snippets = {}
                if "keyword_match_counts" not in row:
                    raise ValueError(
                        f"results line {line_number} is missing keyword_match_counts"
                    )
                keyword_match_counts = row["keyword_match_counts"]
                if not isinstance(keyword_match_counts, dict):
                    raise ValueError(
                        f"results line {line_number} has invalid keyword_match_counts"
                    )

                for keyword in matched_keywords:
                    if not isinstance(keyword, str):
                        continue
                    if keyword not in keyword_match_counts:
                        raise ValueError(
                            f"results line {line_number} is missing count for {keyword!r}"
                        )
                    handle = get_handle(keyword)

                    keyword_row = dict(row)
                    keyword_row["keyword"] = keyword
                    keyword_row["keyword_match_count"] = int(
                        keyword_match_counts[keyword]
                    )
                    keyword_row["keyword_snippets"] = snippets.get(keyword, [])
                    handle.write(json.dumps(keyword_row, sort_keys=True) + "\n")

                    page_counts[keyword] = page_counts.get(keyword, 0) + 1
                    occurrence_counts[keyword] = occurrence_counts.get(
                        keyword, 0
                    ) + int(keyword_row["keyword_match_count"])
                    page_keyword_rows += 1
    finally:
        for handle in handles.values():
            handle.close()

    with temp_summary_path.open("w", encoding="utf-8", newline="") as summary_file:
        writer = csv.DictWriter(
            summary_file,
            fieldnames=["keyword", "pages", "occurrences", "filename"],
        )
        writer.writeheader()
        for keyword in sorted(page_counts):
            writer.writerow(
                {
                    "keyword": keyword,
                    "pages": page_counts[keyword],
                    "occurrences": occurrence_counts[keyword],
                    "filename": filenames[keyword],
                }
            )
    temp_summary_path.replace(summary_path)

    return IndexSummary(
        keywords=len(page_counts),
        page_keyword_rows=page_keyword_rows,
        output_dir=str(directory),
        summary_path=str(summary_path),
    )


def summary_as_dict(summary: IndexSummary) -> dict[str, object]:
    return asdict(summary)
