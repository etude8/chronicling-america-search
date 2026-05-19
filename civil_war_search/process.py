from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
import json
from pathlib import Path
import shutil
import time

from .download import download_record
from .manifest import ArchiveRecord, read_manifest
from .matcher import load_keywords
from .search import search_archive


@dataclass(frozen=True, slots=True)
class ProcessSummary:
    completed: int
    skipped: int
    failed: int
    results_path: str
    parts_dir: str
    state_path: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _state_path_for(output_path: str, state_path: str | None) -> Path:
    if state_path:
        return Path(state_path)
    output = Path(output_path)
    return output.with_suffix(output.suffix + ".process-state.json")


def _parts_dir_for(output_path: str, parts_dir: str | None) -> Path:
    if parts_dir:
        return Path(parts_dir)
    return Path(output_path).with_suffix(Path(output_path).suffix + ".parts")


def _part_path(parts_dir: Path, archive_filename: str) -> Path:
    return parts_dir / f"{archive_filename}.jsonl"


def _read_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"completed_archives": [], "failed_archives": {}}
    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    state.setdefault("completed_archives", [])
    state.setdefault("failed_archives", {})
    return state


def _write_state(path: Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".part")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
    temp_path.replace(path)


def _record_failure(
    state: dict[str, object],
    record: ArchiveRecord,
    error: Exception,
) -> None:
    failed = state.setdefault("failed_archives", {})
    if not isinstance(failed, dict):
        failed = {}
        state["failed_archives"] = failed
    previous = failed.get(record.filename, {})
    attempts = int(previous.get("attempts", 0)) + 1 if isinstance(previous, dict) else 1
    failed[record.filename] = {
        "attempts": attempts,
        "last_error": f"{type(error).__name__}: {error}",
        "last_failed_at": _utc_now(),
    }


def merge_result_parts(parts_dir: str, output_path: str) -> int:
    parts = sorted(Path(parts_dir).glob("*.jsonl"))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_suffix(output.suffix + ".part")
    rows = 0

    with temp_output.open("w", encoding="utf-8") as merged:
        for part_path in parts:
            with part_path.open(encoding="utf-8") as part:
                for line in part:
                    merged.write(line)
                    rows += 1

    temp_output.replace(output)
    return rows


def process_manifest(
    manifest_path: str,
    keywords_path: str,
    output_path: str,
    cache_dir: str,
    start_date: date = date(1860, 1, 1),
    end_date: date = date(1865, 12, 31),
    state_path: str | None = None,
    parts_dir: str | None = None,
    snippet_radius: int = 80,
    verify: bool = True,
    retries: int = 2,
    retry_sleep: float = 5.0,
    keep_archives: bool = False,
) -> ProcessSummary:
    records = read_manifest(manifest_path)
    keywords = load_keywords(keywords_path)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    state_file = _state_path_for(output_path, state_path)
    parts = _parts_dir_for(output_path, parts_dir)
    parts.mkdir(parents=True, exist_ok=True)
    state = _read_state(state_file)
    completed = set(state.get("completed_archives", []))
    completed_this_run = 0
    skipped = 0
    failed_this_run = 0

    for record in records:
        done_part = _part_path(parts, record.filename)
        if record.filename in completed and done_part.exists():
            skipped += 1
            continue

        archive_path: Path | None = None
        temp_part = done_part.with_suffix(done_part.suffix + ".part")
        for attempt in range(retries + 1):
            try:
                downloaded = download_record(record, str(cache), verify)
                archive_path = Path(downloaded.local_path or cache / downloaded.filename)
                stats = search_archive(
                    downloaded,
                    keywords,
                    start_date,
                    end_date,
                    str(temp_part),
                    snippet_radius,
                )
                temp_part.replace(done_part)

                completed.add(record.filename)
                state["completed_archives"] = sorted(completed)
                archive_stats = state.setdefault("archive_stats", {})
                if not isinstance(archive_stats, dict):
                    archive_stats = {}
                    state["archive_stats"] = archive_stats
                archive_stats[record.filename] = asdict(stats)
                failures = state.setdefault("failed_archives", {})
                if isinstance(failures, dict):
                    failures.pop(record.filename, None)
                _write_state(state_file, state)
                completed_this_run += 1
                break
            except Exception as exc:
                temp_part.unlink(missing_ok=True)
                if attempt >= retries:
                    _record_failure(state, record, exc)
                    _write_state(state_file, state)
                    failed_this_run += 1
                else:
                    time.sleep(retry_sleep)
            finally:
                if archive_path is not None and not keep_archives:
                    archive_path.unlink(missing_ok=True)
        else:
            continue

    merge_result_parts(str(parts), output_path)

    return ProcessSummary(
        completed=completed_this_run,
        skipped=skipped,
        failed=failed_this_run,
        results_path=output_path,
        parts_dir=str(parts),
        state_path=str(state_file),
    )
