from __future__ import annotations

from datetime import date
import io
import json
import tarfile

from civil_war_search.manifest import ArchiveRecord, split_manifest, write_manifest
from civil_war_search.process import process_manifest


def _add_text(tar: tarfile.TarFile, name: str, text: str) -> None:
    data = text.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def _sample_archive(path) -> None:
    with tarfile.open(path, "w:bz2") as tar:
        _add_text(
            tar,
            "sn83030214/1863/05/01/ed-1/seq-1/ocr.txt",
            "A report from Fort-Sumter.",
        )
        _add_text(
            tar,
            "sn83030214/1859/05/01/ed-1/seq-1/ocr.txt",
            "Fort Sumter outside range.",
        )


def test_process_manifest_keeps_results_and_removes_archive_cache(tmp_path) -> None:
    source_archive = tmp_path / "source.tar.bz2"
    _sample_archive(source_archive)

    manifest_path = tmp_path / "manifest.jsonl"
    keywords_path = tmp_path / "keywords.txt"
    output_path = tmp_path / "results.jsonl"
    cache_dir = tmp_path / "cache"
    parts_dir = tmp_path / "parts"
    state_path = tmp_path / "state.json"

    write_manifest(
        [
            ArchiveRecord(
                url=source_archive.as_uri(),
                filename="sample.tar.bz2",
            )
        ],
        str(manifest_path),
    )
    keywords_path.write_text("fort sumter\n", encoding="utf-8")

    summary = process_manifest(
        str(manifest_path),
        str(keywords_path),
        str(output_path),
        str(cache_dir),
        start_date=date(1860, 1, 1),
        end_date=date(1865, 12, 31),
        state_path=str(state_path),
        parts_dir=str(parts_dir),
        retry_sleep=0,
    )

    rows = [json.loads(line) for line in output_path.read_text().splitlines()]
    state = json.loads(state_path.read_text())

    assert summary.completed == 1
    assert summary.failed == 0
    assert not (cache_dir / "sample.tar.bz2").exists()
    assert (parts_dir / "sample.tar.bz2.jsonl").exists()
    assert state["completed_archives"] == ["sample.tar.bz2"]
    assert rows[0]["matched_keywords"] == ["fort sumter"]


def test_split_manifest_writes_fixed_size_chunks(tmp_path) -> None:
    manifest_path = tmp_path / "manifest.jsonl"
    chunk_dir = tmp_path / "chunks"
    records = [
        ArchiveRecord(url=f"https://example.test/{index}.tar.bz2", filename=f"{index}.tar.bz2")
        for index in range(5)
    ]
    write_manifest(records, str(manifest_path))

    paths = split_manifest(str(manifest_path), str(chunk_dir), 2)

    assert [path.name for path in paths] == [
        "manifest-0001.jsonl",
        "manifest-0002.jsonl",
        "manifest-0003.jsonl",
    ]
    assert len(paths[0].read_text().splitlines()) == 2
    assert len(paths[2].read_text().splitlines()) == 1
