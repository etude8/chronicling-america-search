from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re


OCR_PATH_RE = re.compile(
    r"(?:^|/)(?P<lccn>[^/]+)/"
    r"(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/"
    r"(?P<edition>ed-\d+)/(?P<sequence>seq-\d+)/ocr\.txt$"
)


@dataclass(frozen=True, slots=True)
class PageIdentity:
    lccn: str
    date: date
    edition: str
    sequence: str

    @property
    def date_text(self) -> str:
        return self.date.isoformat()

    @property
    def page_url(self) -> str:
        return (
            "https://chroniclingamerica.loc.gov/lccn/"
            f"{self.lccn}/{self.date_text}/{self.edition}/{self.sequence}/"
        )


def parse_ocr_member_path(path: str) -> PageIdentity | None:
    match = OCR_PATH_RE.search(path.strip("./"))
    if match is None:
        return None

    try:
        page_date = date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return None

    return PageIdentity(
        lccn=match.group("lccn"),
        date=page_date,
        edition=match.group("edition"),
        sequence=match.group("sequence"),
    )
