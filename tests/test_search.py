from datetime import date
import io
import json
import tarfile

from civil_war_search.manifest import ArchiveRecord
from civil_war_search.search import search_archive


def _add_text(tar: tarfile.TarFile, name: str, text: str) -> None:
    data = text.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def test_search_archive_streams_date_filtered_page_hits(tmp_path) -> None:
    archive_path = tmp_path / "sample.tar.bz2"
    output_path = tmp_path / "out.jsonl"

    with tarfile.open(archive_path, "w:bz2") as tar:
        _add_text(
            tar,
            "sn83030214/1863/05/01/ed-1/seq-1/ocr.txt",
            "A report from Fort-Sumter.",
        )
        _add_text(
            tar,
            "sn83030214/1859/05/01/ed-1/seq-1/ocr.txt",
            "Fort Sumter before the target range.",
        )
        _add_text(
            tar,
            "sn83030214/1863/05/01/ed-1/seq-2/ocr.txt",
            "No matching term here.",
        )

    stats = search_archive(
        ArchiveRecord(
            url="https://example.test/sample.tar.bz2",
            filename="sample.tar.bz2",
            local_path=str(archive_path),
        ),
        ["fort sumter"],
        date(1860, 1, 1),
        date(1865, 12, 31),
        str(output_path),
        snippet_radius=20,
    )

    rows = [json.loads(line) for line in output_path.read_text().splitlines()]
    assert stats.pages_seen == 3
    assert stats.pages_in_range == 2
    assert stats.matched_pages == 1
    assert rows[0]["date"] == "1863-05-01"
    assert rows[0]["matched_keywords"] == ["fort sumter"]
