from datetime import date

from civil_war_search.paths import parse_ocr_member_path


def test_parse_ocr_member_path_builds_page_url() -> None:
    page = parse_ocr_member_path("sn83030214/1863/05/01/ed-1/seq-2/ocr.txt")

    assert page is not None
    assert page.lccn == "sn83030214"
    assert page.date == date(1863, 5, 1)
    assert page.edition == "ed-1"
    assert page.sequence == "seq-2"
    assert (
        page.page_url
        == "https://chroniclingamerica.loc.gov/lccn/sn83030214/1863-05-01/ed-1/seq-2/"
    )


def test_parse_ocr_member_path_rejects_invalid_dates() -> None:
    assert parse_ocr_member_path("sn83030214/1863/99/01/ed-1/seq-2/ocr.txt") is None
    assert parse_ocr_member_path("sn83030214/1863/05/01/ed-1/seq-2/page.jp2") is None
