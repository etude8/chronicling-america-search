from __future__ import annotations

from datetime import date
import json
import time
from urllib.error import HTTPError

from civil_war_search.rate_limits import RateLimit
from civil_war_search.title_api import (
    _chunked,
    _load_resumable_title_rows,
    _progress_line,
    _retry_delay,
    issue_urls_from_calendar,
    page_records_from_issue,
    search_title_manifest,
)


def test_progress_line_includes_counts_and_eta() -> None:
    line = _progress_line("label", 5, 10, time.monotonic() - 5, extra="ok=5")

    assert "label: 5/10" in line
    assert "elapsed=" in line
    assert "eta=" in line
    assert "ok=5" in line


def test_chunked_splits_values() -> None:
    assert list(_chunked(["a", "b", "c"], 2)) == [["a", "b"], ["c"]]


def test_retry_delay_uses_exponential_backoff_without_base_sleep() -> None:
    assert _retry_delay(Exception("x"), 3, 0) == 0


def test_retry_delay_uses_rate_limit_cooldown_for_429() -> None:
    error = HTTPError(
        url="https://www.loc.gov/item/sn83045462/?fo=json",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=None,
    )
    limit = RateLimit(
        name="test",
        requests=20,
        window_seconds=60,
        safety_margin_seconds=0.25,
    )

    assert _retry_delay(error, 0, 2, limit) == 60.25


def test_load_resumable_title_rows_drops_incomplete_issue(tmp_path) -> None:
    output = tmp_path / "title-manifest.jsonl"
    complete_issue = "https://www.loc.gov/item/sn83045462/1861-01-01/ed-1/"
    incomplete_issue = "https://www.loc.gov/item/sn83045462/1861-01-02/ed-1/"
    rows = [
        {
            "issue_url": complete_issue,
            "page_number": 1,
            "medium": "2 pages",
        },
        {
            "issue_url": complete_issue,
            "page_number": 2,
            "medium": "2 pages",
        },
        {
            "issue_url": incomplete_issue,
            "page_number": 1,
            "medium": "2 pages",
        },
    ]
    output.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    kept_rows, completed, dropped = _load_resumable_title_rows(output)

    assert completed == {complete_issue}
    assert dropped == 1
    assert [row["issue_url"] for row in kept_rows] == [complete_issue, complete_issue]


def test_issue_urls_from_calendar_filters_date_range() -> None:
    calendar = {
        "calendar_data": {
            "issue_data": [
                {
                    "date": "1860-12-31",
                    "urls": [{"url": "https://www.loc.gov/item/sn/1860-12-31/ed-1/"}],
                },
                {
                    "date": "1861-01-01",
                    "urls": [{"url": "https://www.loc.gov/item/sn/1861-01-01/ed-1/"}],
                },
            ]
        }
    }

    urls = issue_urls_from_calendar(
        calendar,
        date(1861, 1, 1),
        date(1861, 12, 31),
    )

    assert urls == ["https://www.loc.gov/item/sn/1861-01-01/ed-1/"]


def test_page_records_from_issue_extracts_media_links() -> None:
    records = page_records_from_issue(
        "https://www.loc.gov/item/sn83045462/1861-01-21/ed-1/",
        {
            "item": {
                "title": "Evening star (Washington, D.C.), January 21, 1861",
                "medium": "4 pages",
            },
            "resources": [
                {
                    "url": "https://www.loc.gov/resource/sn83045462/1861-01-21/ed-1/",
                    "image": "https://example.test/thumb.jpg",
                    "files": [
                        [
                            {
                                "mimetype": "application/pdf",
                                "url": "https://example.test/page.pdf",
                            },
                            {
                                "mimetype": "image/jp2",
                                "url": "https://example.test/page.jp2",
                                "info": "https://example.test/info.json",
                            },
                            {
                                "mimetype": "text/xml",
                                "url": "https://example.test/page.xml",
                            },
                            {
                                "mimetype": "image/jpeg",
                                "url": "https://example.test/page.jpg",
                                "width": 800,
                            },
                            {
                                "mimetype": "text/plain",
                                "fulltext_service": "https://example.test/text",
                            },
                        ]
                    ],
                }
            ],
        },
    )

    assert records == [
        {
            "lccn": "sn83045462",
            "title": "Evening star (Washington, D.C.), January 21, 1861",
            "date": "1861-01-21",
            "edition": "ed-1",
            "page_number": 1,
            "issue_url": "https://www.loc.gov/item/sn83045462/1861-01-21/ed-1/",
            "resource_url": "https://www.loc.gov/resource/sn83045462/1861-01-21/ed-1/",
            "page_url": "https://www.loc.gov/resource/sn83045462/1861-01-21/ed-1/?sp=1",
            "pdf_url": "https://example.test/page.pdf",
            "jp2_url": "https://example.test/page.jp2",
            "alto_xml_url": "https://example.test/page.xml",
            "text_url": "https://example.test/text",
            "image_url": "https://example.test/page.jpg",
            "iiif_info_url": "https://example.test/info.json",
            "medium": "4 pages",
        }
    ]


def test_search_title_manifest_uses_text_service_json(tmp_path) -> None:
    text_response = tmp_path / "text.json"
    manifest = tmp_path / "title-manifest.jsonl"
    keywords = tmp_path / "keywords.txt"
    output = tmp_path / "hits.jsonl"
    failures = tmp_path / "failures.jsonl"

    text_response.write_text(
        json.dumps({"/page.xml": {"full_text": "Fort Sumter and more Fort-Sumter."}}),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "lccn": "sn83045462",
                "title": "Evening star",
                "date": "1861-01-21",
                "edition": "ed-1",
                "page_number": 1,
                "page_url": "https://www.loc.gov/resource/sn83045462/1861-01-21/ed-1/?sp=1",
                "pdf_url": "https://example.test/page.pdf",
                "image_url": "https://example.test/page.jpg",
                "text_url": text_response.as_uri(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    keywords.write_text("fort sumter\n", encoding="utf-8")

    summary = search_title_manifest(
        str(manifest),
        str(keywords),
        str(output),
        failures_path=str(failures),
        request_sleep=0,
        retry_sleep=0,
        progress=False,
        workers=2,
    )

    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert summary.pages_seen == 1
    assert summary.pages_searched == 1
    assert summary.matched_pages == 1
    assert summary.failed_pages == 0
    assert rows[0]["source"] == "title-api"
    assert rows[0]["matched_keywords"] == ["fort sumter"]
    assert rows[0]["keyword_match_counts"] == {"fort sumter": 2}


def test_search_title_manifest_applies_keyword_groups(tmp_path) -> None:
    text_response = tmp_path / "text.json"
    manifest = tmp_path / "title-manifest.jsonl"
    groups = tmp_path / "groups.json"
    output = tmp_path / "hits.jsonl"
    failures = tmp_path / "failures.jsonl"

    text_response.write_text(
        json.dumps({"/page.xml": {"full_text": "The criminal court imposed a fine."}}),
        encoding="utf-8",
    )
    manifest.write_text(
        json.dumps(
            {
                "page_url": "https://www.loc.gov/resource/sn83045462/1861-01-21/ed-1/?sp=1",
                "text_url": text_response.as_uri(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    groups.write_text(
        json.dumps(
            {
                "groups": [
                    {"name": "anchors", "keywords": ["criminal court"]},
                    {
                        "name": "broad",
                        "require_any": ["anchors"],
                        "keywords": ["fine"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = search_title_manifest(
        str(manifest),
        None,
        str(output),
        failures_path=str(failures),
        request_sleep=0,
        retry_sleep=0,
        keyword_groups_path=str(groups),
        progress=False,
        workers=2,
    )

    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert summary.matched_pages == 1
    assert rows[0]["matched_keywords"] == ["criminal court", "fine"]
    assert rows[0]["matched_groups"] == {
        "anchors": ["criminal court"],
        "broad": ["fine"],
    }
