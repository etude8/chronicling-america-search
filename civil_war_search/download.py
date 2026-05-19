from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from pathlib import Path
import shutil
from urllib.request import Request, urlopen

from .manifest import ArchiveRecord, read_manifest, write_manifest
from .rate_limits import LOC_BULK_OCR_LIMITER, is_bulk_ocr_url


def sha1_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while chunk := handle.read(block_size):
            digest.update(chunk)
    return digest.hexdigest()


def download_record(record: ArchiveRecord, output_dir: str, verify: bool) -> ArchiveRecord:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / record.filename
    expected_sha1 = record.sha1.lower() if record.sha1 else None

    if target.exists():
        if not verify or expected_sha1 is None or sha1_file(target) == expected_sha1:
            record.local_path = str(target)
            record.status = "downloaded"
            return record

    temp_target = target.with_suffix(target.suffix + ".part")
    if is_bulk_ocr_url(record.url):
        LOC_BULK_OCR_LIMITER.wait()
    request = Request(record.url, headers={"User-Agent": "civil-war-search/0.1"})
    try:
        with urlopen(request, timeout=120) as response, temp_target.open("wb") as handle:
            shutil.copyfileobj(response, handle, length=1024 * 1024)
    except Exception:
        temp_target.unlink(missing_ok=True)
        raise

    if verify and expected_sha1 is not None:
        actual_sha1 = sha1_file(temp_target)
        if actual_sha1 != expected_sha1:
            temp_target.unlink(missing_ok=True)
            raise ValueError(
                f"checksum mismatch for {record.filename}: "
                f"expected {expected_sha1}, got {actual_sha1}"
            )

    temp_target.replace(target)
    record.local_path = str(target)
    record.status = "downloaded"
    return record


def download_archives(
    manifest_path: str,
    output_dir: str,
    workers: int,
    verify: bool = True,
) -> list[ArchiveRecord]:
    records = read_manifest(manifest_path)
    by_filename = {record.filename: record for record in records}
    pending = [record for record in records if record.status != "downloaded"]

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(download_record, record, output_dir, verify): record
            for record in pending
        }
        for future in as_completed(futures):
            updated = future.result()
            by_filename[updated.filename] = updated
            write_manifest(by_filename.values(), manifest_path)

    return list(by_filename.values())
