from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path
import re
import time
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .keyword_groups import (
    filter_matches_by_groups,
    groups_as_dict,
    load_keyword_plan,
)
from .matcher import build_matcher, normalize_text


ISSUE_URL_RE = re.compile(
    r"/item/(?P<lccn>[^/]+)/(?P<date>\d{4}-\d{2}-\d{2})/(?P<edition>ed-\d+)/"
)


@dataclass(frozen=True, slots=True)
class TitleManifestSummary:
    issues: int
    pages: int
    failed_issues: int
    output_path: str
    failures_path: str


@dataclass(frozen=True, slots=True)
class TitleSearchSummary:
    pages_seen: int
    pages_searched: int
    matched_pages: int
    failed_pages: int
    output_path: str
    failures_path: str


def _request_url(url: str, timeout: int = 60) -> bytes:
    request = Request(url, headers={"User-Agent": "civil-war-search/0.1"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _request_url_with_retries(url: str, retries: int, retry_sleep: float) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _request_url(url)
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(retry_sleep)
    if last_error is None:
        raise RuntimeError(f"failed to fetch {url}")
    raise last_error


def _read_json_url(url: str, timeout: int = 60) -> Any:
    return json.loads(_request_url(url, timeout).decode("utf-8"))


def _with_json_format(url: str) -> str:
    separator = "&" if "?" in url else "?"
    return url if "fo=json" in url else f"{url}{separator}fo=json"


def _calendar_url(lccn: str, year: int) -> str:
    query = urlencode({"st": "calendar", "year": year, "fo": "json"})
    return f"https://www.loc.gov/item/{lccn}/?{query}"


def _parse_issue_url(url: str) -> tuple[str, date, str] | None:
    match = ISSUE_URL_RE.search(url)
    if match is None:
        return None
    return (
        match.group("lccn"),
        date.fromisoformat(match.group("date")),
        match.group("edition"),
    )


def issue_urls_from_calendar(
    calendar_json: dict[str, Any],
    start_date: date,
    end_date: date,
) -> list[str]:
    issue_urls: list[str] = []
    calendar_data = calendar_json.get("calendar_data", {})
    if not isinstance(calendar_data, dict):
        return issue_urls

    issue_data = calendar_data.get("issue_data", [])
    if not isinstance(issue_data, list):
        return issue_urls

    for issue in issue_data:
        if not isinstance(issue, dict):
            continue
        raw_date = issue.get("date")
        if not isinstance(raw_date, str):
            continue
        try:
            issue_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        if issue_date < start_date or issue_date > end_date:
            continue

        for url_info in issue.get("urls", []):
            if isinstance(url_info, dict) and isinstance(url_info.get("url"), str):
                issue_urls.append(url_info["url"])

    return issue_urls


def _file_url(files: list[dict[str, Any]], mimetype: str) -> str | None:
    for file_info in files:
        if file_info.get("mimetype") == mimetype and isinstance(
            file_info.get("url"), str
        ):
            return file_info["url"]
    return None


def _text_service_url(files: list[dict[str, Any]]) -> str | None:
    for file_info in files:
        if file_info.get("mimetype") == "text/plain":
            fulltext = file_info.get("fulltext_service")
            if isinstance(fulltext, str):
                return fulltext
    return None


def _iiif_info_url(files: list[dict[str, Any]]) -> str | None:
    for file_info in files:
        info = file_info.get("info")
        if isinstance(info, str):
            return info
    return None


def _best_jpeg_url(files: list[dict[str, Any]], fallback: str | None) -> str | None:
    jpeg_files = [
        file_info
        for file_info in files
        if file_info.get("mimetype") == "image/jpeg" and isinstance(file_info.get("url"), str)
    ]
    if not jpeg_files:
        return fallback
    return max(jpeg_files, key=lambda item: int(item.get("width", 0))).get("url")


def page_records_from_issue(
    issue_url: str,
    issue_json: dict[str, Any],
) -> list[dict[str, Any]]:
    parsed = _parse_issue_url(issue_url)
    item = issue_json.get("item", {})
    resources = issue_json.get("resources", [])
    if parsed is None or not isinstance(item, dict) or not resources:
        return []

    lccn, issue_date, edition = parsed
    resource = resources[0]
    if not isinstance(resource, dict):
        return []
    page_files = resource.get("files", [])
    if not isinstance(page_files, list):
        return []

    title = item.get("title")
    medium = item.get("medium")
    resource_url = resource.get("url") if isinstance(resource.get("url"), str) else None
    fallback_image = resource.get("image") if isinstance(resource.get("image"), str) else None

    records: list[dict[str, Any]] = []
    for index, files in enumerate(page_files, start=1):
        if not isinstance(files, list):
            continue
        typed_files = [file_info for file_info in files if isinstance(file_info, dict)]
        page_url = f"https://www.loc.gov/resource/{lccn}/{issue_date.isoformat()}/{edition}/?sp={index}"
        records.append(
            {
                "lccn": lccn,
                "title": title,
                "date": issue_date.isoformat(),
                "edition": edition,
                "page_number": index,
                "issue_url": issue_url,
                "resource_url": resource_url,
                "page_url": page_url,
                "pdf_url": _file_url(typed_files, "application/pdf"),
                "jp2_url": _file_url(typed_files, "image/jp2"),
                "alto_xml_url": _file_url(typed_files, "text/xml"),
                "text_url": _text_service_url(typed_files),
                "image_url": _best_jpeg_url(typed_files, fallback_image),
                "iiif_info_url": _iiif_info_url(typed_files),
                "medium": medium,
            }
        )
    return records


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            count += 1
    return count


def _fetch_with_retries(url: str, retries: int, retry_sleep: float) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _read_json_url(_with_json_format(url))
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(retry_sleep)
    if last_error is None:
        raise RuntimeError(f"failed to fetch {url}")
    raise last_error


def build_title_manifest(
    lccn: str,
    start_date: date,
    end_date: date,
    output_path: str,
    failures_path: str | None = None,
    retries: int = 2,
    retry_sleep: float = 2.0,
    request_sleep: float = 0.1,
    max_issues: int | None = None,
) -> TitleManifestSummary:
    output = Path(output_path)
    failures = Path(failures_path) if failures_path else output.with_suffix(
        output.suffix + ".failures.jsonl"
    )
    issue_urls: list[str] = []

    for year in range(start_date.year, end_date.year + 1):
        calendar_json = _fetch_with_retries(_calendar_url(lccn, year), retries, retry_sleep)
        issue_urls.extend(issue_urls_from_calendar(calendar_json, start_date, end_date))
        if request_sleep:
            time.sleep(request_sleep)

    if max_issues is not None:
        issue_urls = issue_urls[:max_issues]

    output.parent.mkdir(parents=True, exist_ok=True)
    failures.parent.mkdir(parents=True, exist_ok=True)
    issues = 0
    pages = 0
    failed_rows: list[dict[str, Any]] = []

    with output.open("w", encoding="utf-8") as handle:
        for issue_url in issue_urls:
            try:
                issue_json = _fetch_with_retries(issue_url, retries, retry_sleep)
                records = page_records_from_issue(issue_url, issue_json)
                for record in records:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
                issues += 1
                pages += len(records)
            except Exception as exc:
                failed_rows.append(
                    {
                        "issue_url": issue_url,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            if request_sleep:
                time.sleep(request_sleep)

    _write_jsonl(failures, failed_rows)
    return TitleManifestSummary(
        issues=issues,
        pages=pages,
        failed_issues=len(failed_rows),
        output_path=str(output),
        failures_path=str(failures),
    )


def _plain_text_from_response(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text

    if isinstance(payload, dict):
        parts: list[str] = []
        for value in payload.values():
            if isinstance(value, dict) and isinstance(value.get("full_text"), str):
                parts.append(value["full_text"])
        if parts:
            return "\n".join(parts)
    return text


def _snippets(
    matches: dict[str, list[int]],
    normalized_text: str,
    snippet_radius: int,
) -> dict[str, list[str]]:
    if snippet_radius <= 0:
        return {}
    snippets: dict[str, list[str]] = {}
    for keyword, positions in matches.items():
        snippets[keyword] = []
        for position in positions[:3]:
            start = max(0, position - snippet_radius)
            end = min(len(normalized_text), position + len(keyword) + snippet_radius)
            snippets[keyword].append(normalized_text[start:end].strip())
    return snippets


def search_title_manifest(
    manifest_path: str,
    keywords_path: str | None,
    output_path: str,
    failures_path: str | None = None,
    retries: int = 2,
    retry_sleep: float = 2.0,
    request_sleep: float = 0.05,
    snippet_radius: int = 80,
    keyword_groups_path: str | None = None,
) -> TitleSearchSummary:
    manifest = Path(manifest_path)
    output = Path(output_path)
    failures = Path(failures_path) if failures_path else output.with_suffix(
        output.suffix + ".failures.jsonl"
    )
    keyword_plan = load_keyword_plan(keywords_path, keyword_groups_path)
    matcher = build_matcher(keyword_plan.keywords)
    pages_seen = 0
    pages_searched = 0
    matched_pages = 0
    failed_rows: list[dict[str, Any]] = []
    temp_output = output.with_suffix(output.suffix + ".part")

    output.parent.mkdir(parents=True, exist_ok=True)
    failures.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open(encoding="utf-8") as input_file, temp_output.open(
        "w", encoding="utf-8"
    ) as result_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            pages_seen += 1
            row = json.loads(line)
            text_url = row.get("text_url")
            if not isinstance(text_url, str):
                failed_rows.append(
                    {
                        "line": line_number,
                        "page_url": row.get("page_url"),
                        "error": "missing text_url",
                    }
                )
                continue

            try:
                raw = _request_url_with_retries(text_url, retries, retry_sleep)
                page_text = _plain_text_from_response(raw)
                normalized = normalize_text(page_text)
                matches = matcher.find(normalized)
                matches, matched_groups = filter_matches_by_groups(
                    matches,
                    keyword_plan,
                )
                pages_searched += 1
                if matches:
                    matched_pages += 1
                    result = dict(row)
                    result.update(
                        {
                            "matched_keywords": sorted(matches),
                            "keyword_match_counts": {
                                keyword: len(positions)
                                for keyword, positions in sorted(matches.items())
                            },
                            "match_count": sum(
                                len(positions) for positions in matches.values()
                            ),
                            "snippets": _snippets(matches, normalized, snippet_radius),
                            "source": "title-api",
                        }
                    )
                    if matched_groups:
                        result["matched_groups"] = groups_as_dict(matched_groups)
                    result_file.write(json.dumps(result, sort_keys=True) + "\n")
            except Exception as exc:
                failed_rows.append(
                    {
                        "line": line_number,
                        "page_url": row.get("page_url"),
                        "text_url": text_url,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            if request_sleep:
                time.sleep(request_sleep)

    temp_output.replace(output)
    _write_jsonl(failures, failed_rows)
    return TitleSearchSummary(
        pages_seen=pages_seen,
        pages_searched=pages_searched,
        matched_pages=matched_pages,
        failed_pages=len(failed_rows),
        output_path=str(output),
        failures_path=str(failures),
    )


def summary_as_dict(summary: TitleManifestSummary | TitleSearchSummary) -> dict[str, Any]:
    return asdict(summary)
