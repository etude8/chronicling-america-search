from __future__ import annotations

import csv
import json

import pytest

from civil_war_search.index import index_results, keyword_slug


def test_keyword_slug_is_filesystem_friendly() -> None:
    assert keyword_slug("Fort Sumter!") == "fort-sumter"
    assert keyword_slug("  ") == "keyword"


def test_index_results_writes_keyword_files_and_summary(tmp_path) -> None:
    results_path = tmp_path / "pages.jsonl"
    output_dir = tmp_path / "by-keyword"
    rows = [
        {
            "page_url": "https://example.test/1",
            "matched_keywords": ["fort sumter", "war"],
            "keyword_match_counts": {"fort sumter": 1, "war": 2},
            "match_count": 3,
            "snippets": {"fort sumter": ["fort sumter text"], "war": ["war text"]},
        },
        {
            "page_url": "https://example.test/2",
            "matched_keywords": ["war"],
            "keyword_match_counts": {"war": 1},
            "match_count": 1,
            "snippets": {"war": ["more war"]},
        },
    ]
    results_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    summary = index_results(str(results_path), str(output_dir))

    fort_rows = [
        json.loads(line)
        for line in (output_dir / "fort-sumter.jsonl").read_text().splitlines()
    ]
    war_rows = [
        json.loads(line)
        for line in (output_dir / "war.jsonl").read_text().splitlines()
    ]
    with (output_dir / "keyword-summary.csv").open(encoding="utf-8", newline="") as file:
        summary_rows = list(csv.DictReader(file))

    assert summary.keywords == 2
    assert summary.page_keyword_rows == 3
    assert fort_rows[0]["keyword"] == "fort sumter"
    assert fort_rows[0]["keyword_snippets"] == ["fort sumter text"]
    assert len(war_rows) == 2
    assert summary_rows == [
        {
            "keyword": "fort sumter",
            "pages": "1",
            "occurrences": "1",
            "filename": "fort-sumter.jsonl",
        },
        {
            "keyword": "war",
            "pages": "2",
            "occurrences": "3",
            "filename": "war.jsonl",
        },
    ]


def test_index_results_limits_open_files(tmp_path) -> None:
    results_path = tmp_path / "pages.jsonl"
    output_dir = tmp_path / "by-keyword"
    results_path.write_text(
        "".join(
            json.dumps(
                {
                    "page_url": f"https://example.test/{index}",
                    "matched_keywords": [f"keyword {index}"],
                    "keyword_match_counts": {f"keyword {index}": 1},
                    "match_count": 1,
                    "snippets": {},
                }
            )
            + "\n"
            for index in range(3)
        ),
        encoding="utf-8",
    )

    summary = index_results(str(results_path), str(output_dir), max_open_files=1)

    assert summary.keywords == 3
    assert (output_dir / "keyword-0.jsonl").exists()
    assert (output_dir / "keyword-1.jsonl").exists()
    assert (output_dir / "keyword-2.jsonl").exists()


def test_index_results_requires_keyword_match_counts(tmp_path) -> None:
    results_path = tmp_path / "pages.jsonl"
    output_dir = tmp_path / "by-keyword"
    results_path.write_text(
        json.dumps(
            {
                "page_url": "https://example.test/1",
                "matched_keywords": ["war"],
                "match_count": 1,
                "snippets": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="keyword_match_counts"):
        index_results(str(results_path), str(output_dir))
