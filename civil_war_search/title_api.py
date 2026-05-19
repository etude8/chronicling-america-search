from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import random
import re
import sys
import time
from typing import Any, Iterable
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .keyword_groups import (
    filter_matches_by_groups,
    groups_as_dict,
    load_keyword_plan,
)
from .matcher import build_matcher, normalize_text
from .rate_limits import (
    LOC_JSON_API_LIMITER,
    LOC_JSON_API_RATE_LIMIT,
    LOC_MICROSERVICE_LIMITER,
    LOC_MICROSERVICE_RATE_LIMIT,
    RateLimit,
    RateLimiter,
    is_remote_url,
)


ISSUE_URL_RE = re.compile(
    r"/item/(?P<lccn>[^/]+)/(?P<date>\d{4}-\d{2}-\d{2})/(?P<edition>ed-\d+)/"
)
PAGES_RE = re.compile(r"(?P<pages>\d+)\s+pages?", re.IGNORECASE)


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


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


def _progress(
    message: str,
    enabled: bool,
) -> None:
    if enabled:
        print(message, file=sys.stderr, flush=True)


def _progress_line(
    label: str,
    current: int,
    total: int,
    started_at: float,
    extra: str = "",
) -> str:
    elapsed = time.monotonic() - started_at
    rate = current / elapsed if elapsed > 0 else 0.0
    remaining = total - current
    eta = remaining / rate if rate > 0 and total else 0.0
    suffix = f" {extra}" if extra else ""
    return (
        f"{label}: {current}/{total} "
        f"elapsed={_format_duration(elapsed)} "
        f"rate={rate:.2f}/s "
        f"eta={_format_duration(eta)}{suffix}"
    )


def _rate_limit_label(rate_limit: RateLimit) -> str:
    window_seconds = int(rate_limit.window_seconds)
    if rate_limit.paced_requests == rate_limit.requests:
        return f"rate_limit={rate_limit.requests}/{window_seconds}s"
    return (
        f"paced_limit={rate_limit.paced_requests}/{window_seconds}s "
        f"(cap={rate_limit.requests}/{window_seconds}s)"
    )


def _chunked(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _request_url(
    url: str,
    timeout: int = 60,
    limiter: RateLimiter | None = None,
) -> bytes:
    if limiter is not None and is_remote_url(url):
        limiter.wait()
    request = Request(url, headers={"User-Agent": "civil-war-search/0.1"})
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _request_url_with_retries(
    url: str,
    retries: int,
    retry_sleep: float,
    timeout: int = 30,
    limiter: RateLimiter | None = None,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _request_url(url, timeout=timeout, limiter=limiter)
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                _sleep_before_retry(exc, attempt, retry_sleep, limiter)
    if last_error is None:
        raise RuntimeError(f"failed to fetch {url}")
    raise last_error


def _read_json_url(
    url: str,
    timeout: int = 60,
    limiter: RateLimiter | None = None,
) -> Any:
    return json.loads(
        _request_url(url, timeout=timeout, limiter=limiter).decode("utf-8")
    )


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


def _fetch_with_retries(
    url: str,
    retries: int,
    retry_sleep: float,
    timeout: int = 60,
) -> Any:
    last_error: Exception | None = None
    limiter = LOC_JSON_API_LIMITER
    for attempt in range(retries + 1):
        try:
            return _read_json_url(
                _with_json_format(url),
                timeout=timeout,
                limiter=limiter,
            )
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                _sleep_before_retry(exc, attempt, retry_sleep, limiter)
    if last_error is None:
        raise RuntimeError(f"failed to fetch {url}")
    raise last_error


def _sleep_before_retry(
    error: Exception,
    attempt: int,
    base_sleep: float,
    limiter: RateLimiter | None,
) -> None:
    delay = _retry_delay(
        error,
        attempt,
        base_sleep,
        limiter.limit if limiter is not None else None,
    )
    if limiter is not None and _is_rate_limited_error(error):
        limiter.defer(delay)
    time.sleep(delay)


def _retry_delay(
    error: Exception,
    attempt: int,
    base_sleep: float,
    rate_limit: RateLimit | None = None,
) -> float:
    retry_after = getattr(getattr(error, "headers", None), "get", lambda _name: None)(
        "Retry-After"
    )
    if retry_after:
        try:
            return max(float(retry_after), base_sleep)
        except ValueError:
            parsed_retry_after = _parse_retry_after_date(retry_after)
            if parsed_retry_after is not None:
                return max(parsed_retry_after, base_sleep)

    if rate_limit is not None and _is_rate_limited_error(error):
        return max(float(base_sleep), float(rate_limit.retry_cooldown_seconds))

    delay = base_sleep * (2**attempt)
    if delay <= 0:
        return 0.0
    return delay + random.uniform(0.0, delay * 0.25)


def _parse_retry_after_date(value: str) -> float | None:
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        return None
    return max(0.0, retry_at.timestamp() - time.time())


def _is_rate_limited_error(error: Exception) -> bool:
    if isinstance(error, HTTPError):
        return error.code == 429
    status = getattr(error, "status", None)
    code = getattr(error, "code", None)
    return status == 429 or code == 429


def _expected_pages(rows: list[dict[str, Any]]) -> int | None:
    for row in rows:
        medium = row.get("medium")
        if isinstance(medium, str):
            match = PAGES_RE.search(medium)
            if match:
                return int(match.group("pages"))
    return None


def _complete_issue_rows(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    page_numbers = {
        page_number
        for row in rows
        if isinstance((page_number := row.get("page_number")), int)
    }
    expected = _expected_pages(rows)
    if expected is not None:
        return page_numbers == set(range(1, expected + 1))
    if not page_numbers:
        return True
    return page_numbers == set(range(1, max(page_numbers) + 1))


def _load_resumable_title_rows(
    output: Path,
) -> tuple[list[dict[str, Any]], set[str], int]:
    if not output.exists():
        return [], set(), 0

    by_issue: dict[str, list[dict[str, Any]]] = {}
    with output.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            issue_url = row.get("issue_url")
            if isinstance(issue_url, str):
                by_issue.setdefault(issue_url, []).append(row)

    kept_rows: list[dict[str, Any]] = []
    completed: set[str] = set()
    dropped = 0
    for issue_url, rows in by_issue.items():
        if _complete_issue_rows(rows):
            completed.add(issue_url)
            kept_rows.extend(rows)
        else:
            dropped += len(rows)

    return kept_rows, completed, dropped


def build_title_manifest(
    lccn: str,
    start_date: date,
    end_date: date,
    output_path: str,
    failures_path: str | None = None,
    retries: int = 2,
    retry_sleep: float = 2.0,
    request_sleep: float = 0.0,
    max_issues: int | None = None,
    timeout: int = 20,
    progress_every: int = 25,
    progress: bool = True,
    workers: int = 2,
    batch_size: int = 50,
) -> TitleManifestSummary:
    output = Path(output_path)
    failures = Path(failures_path) if failures_path else output.with_suffix(
        output.suffix + ".failures.jsonl"
    )
    issue_urls: list[str] = []
    failed_rows: list[dict[str, Any]] = []
    calendar_started_at = time.monotonic()
    total_years = end_date.year - start_date.year + 1

    _progress(
        f"title-manifest: fetching calendars for {lccn} "
        f"({start_date.isoformat()} to {end_date.isoformat()}); "
        f"{_rate_limit_label(LOC_JSON_API_RATE_LIMIT)}",
        progress,
    )
    for year_index, year in enumerate(range(start_date.year, end_date.year + 1), start=1):
        calendar_url = _calendar_url(lccn, year)
        try:
            calendar_json = _fetch_with_retries(
                calendar_url,
                retries,
                retry_sleep,
                timeout=timeout,
            )
            issue_urls.extend(
                issue_urls_from_calendar(calendar_json, start_date, end_date)
            )
        except Exception as exc:
            failed_rows.append(
                {
                    "calendar_year": year,
                    "calendar_url": calendar_url,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        _progress(
            _progress_line(
                "title-manifest calendars",
                year_index,
                total_years,
                calendar_started_at,
                extra=f"issues_found={len(issue_urls)} calendar_failures={len(failed_rows)}",
            ),
            progress,
        )
        if request_sleep:
            time.sleep(request_sleep)

    if max_issues is not None:
        issue_urls = issue_urls[:max_issues]

    output.parent.mkdir(parents=True, exist_ok=True)
    failures.parent.mkdir(parents=True, exist_ok=True)
    kept_rows, completed_issue_urls, dropped_rows = _load_resumable_title_rows(output)
    requested_issue_urls = set(issue_urls)
    kept_rows = [
        row
        for row in kept_rows
        if isinstance(row.get("issue_url"), str)
        and row["issue_url"] in requested_issue_urls
    ]
    completed_issue_urls = completed_issue_urls & requested_issue_urls
    issue_urls_to_fetch = [
        issue_url for issue_url in issue_urls if issue_url not in completed_issue_urls
    ]
    issues = len(completed_issue_urls)
    pages = len(kept_rows)
    issue_started_at = time.monotonic()
    total_issues = len(issue_urls_to_fetch)

    _progress(
        (
            f"title-manifest: fetching {total_issues} issue records with "
            f"{max(1, workers)} worker(s), batch_size={max(1, batch_size)}; "
            f"{_rate_limit_label(LOC_JSON_API_RATE_LIMIT)}; "
            f"skipped={len(completed_issue_urls)} "
            f"dropped_partial_rows={dropped_rows}"
        ),
        progress,
    )

    temp_output = output.with_suffix(output.suffix + ".part")
    with temp_output.open("w", encoding="utf-8") as handle:
        for row in kept_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    temp_output.replace(output)

    with output.open("a", encoding="utf-8") as handle:
        issue_index = 0
        try:
            for batch in _chunked(issue_urls_to_fetch, max(1, batch_size)):
                with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
                    futures = [
                        executor.submit(
                            _fetch_issue_records,
                            issue_url,
                            retries,
                            retry_sleep,
                            timeout,
                        )
                        for issue_url in batch
                    ]

                    for future in as_completed(futures):
                        issue_index += 1
                        issue_url, records, error = future.result()
                        if error is None:
                            for record in records:
                                handle.write(json.dumps(record, sort_keys=True) + "\n")
                            handle.flush()
                            issues += 1
                            pages += len(records)
                        else:
                            failed_rows.append(
                                {
                                    "issue_url": issue_url,
                                    "error": error,
                                }
                            )
                        if (
                            progress
                            and (
                                issue_index == 1
                                or issue_index == total_issues
                                or (
                                    progress_every > 0
                                    and issue_index % progress_every == 0
                                )
                            )
                        ):
                            _progress(
                                _progress_line(
                                    "title-manifest issues",
                                    issue_index,
                                    total_issues,
                                    issue_started_at,
                                    extra=(
                                        f"ok_total={issues} "
                                        f"failed={len(failed_rows)} "
                                        f"pages={pages} out={output}"
                                    ),
                                ),
                                progress,
                            )
                        if request_sleep:
                            time.sleep(request_sleep)
        except KeyboardInterrupt:
            _write_jsonl(failures, failed_rows)
            _progress(
                (
                    "title-manifest: interrupted; completed issue rows already "
                    f"written to {output}. Rerun the same command to resume."
                ),
                progress,
            )
            raise

    _write_jsonl(failures, failed_rows)
    return TitleManifestSummary(
        issues=issues,
        pages=pages,
        failed_issues=len(failed_rows),
        output_path=str(output),
        failures_path=str(failures),
    )


def _fetch_issue_records(
    issue_url: str,
    retries: int,
    retry_sleep: float,
    timeout: int,
) -> tuple[str, list[dict[str, Any]], str | None]:
    try:
        issue_json = _fetch_with_retries(
            issue_url,
            retries,
            retry_sleep,
            timeout=timeout,
        )
        return issue_url, page_records_from_issue(issue_url, issue_json), None
    except Exception as exc:
        return issue_url, [], f"{type(exc).__name__}: {exc}"


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
    request_sleep: float = 0.0,
    snippet_radius: int = 80,
    keyword_groups_path: str | None = None,
    timeout: int = 60,
    progress_every: int = 100,
    progress: bool = True,
    workers: int = 2,
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
    page_jobs = [
        (line_number, json.loads(line))
        for line_number, line in enumerate(manifest.open(encoding="utf-8"), start=1)
        if line.strip()
    ]
    total_pages = len(page_jobs)
    started_at = time.monotonic()

    output.parent.mkdir(parents=True, exist_ok=True)
    failures.parent.mkdir(parents=True, exist_ok=True)
    _progress(
        f"search-title: searching {total_pages} pages from {manifest} "
        f"with {max(1, workers)} worker(s); "
        f"{_rate_limit_label(LOC_MICROSERVICE_RATE_LIMIT)}",
        progress,
    )
    with temp_output.open("w", encoding="utf-8") as result_file:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = [
                executor.submit(
                    _fetch_title_text,
                    line_number,
                    row,
                    retries,
                    retry_sleep,
                    timeout,
                )
                for line_number, row in page_jobs
            ]

            for future in as_completed(futures):
                line_number, row, raw, error = future.result()
                pages_seen += 1
                if error is not None:
                    failed_rows.append(error)
                else:
                    if raw is None:
                        failed_rows.append(
                            {
                                "line": line_number,
                                "page_url": row.get("page_url"),
                                "error": "missing text response",
                            }
                        )
                    else:
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
                                    "snippets": _snippets(
                                        matches,
                                        normalized,
                                        snippet_radius,
                                    ),
                                    "source": "title-api",
                                }
                            )
                            if matched_groups:
                                result["matched_groups"] = groups_as_dict(matched_groups)
                            result_file.write(json.dumps(result, sort_keys=True) + "\n")
                if (
                    progress
                    and (
                        pages_seen == 1
                        or pages_seen == total_pages
                        or (progress_every > 0 and pages_seen % progress_every == 0)
                    )
                ):
                    _progress(
                        _progress_line(
                            "search-title pages",
                            pages_seen,
                            total_pages,
                            started_at,
                            extra=(
                                f"searched={pages_searched} matched={matched_pages} "
                                f"failed={len(failed_rows)} out={output}"
                            ),
                        ),
                        progress,
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


def _fetch_title_text(
    line_number: int,
    row: dict[str, Any],
    retries: int,
    retry_sleep: float,
    timeout: int,
) -> tuple[int, dict[str, Any], bytes | None, dict[str, Any] | None]:
    text_url = row.get("text_url")
    if not isinstance(text_url, str):
        return (
            line_number,
            row,
            None,
            {
                "line": line_number,
                "page_url": row.get("page_url"),
                "error": "missing text_url",
            },
        )

    try:
        raw = _request_url_with_retries(
            text_url,
            retries,
            retry_sleep,
            timeout=timeout,
            limiter=LOC_MICROSERVICE_LIMITER,
        )
        return line_number, row, raw, None
    except Exception as exc:
        return (
            line_number,
            row,
            None,
            {
                "line": line_number,
                "page_url": row.get("page_url"),
                "text_url": text_url,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )


def summary_as_dict(summary: TitleManifestSummary | TitleSearchSummary) -> dict[str, Any]:
    return asdict(summary)
