from civil_war_search.manifest import records_from_html, records_from_payload


def test_records_from_payload_accepts_ocr_json_like_items() -> None:
    records = records_from_payload(
        [
            {
                "filename": "dlc_test_ver01.tar.bz2",
                "url": "https://chroniclingamerica.loc.gov/ocr/dlc_test_ver01.tar.bz2",
                "created": "2024-01-01T00:00:00-05:00",
                "size": "1.0 MB",
                "sha1": "abc123",
            }
        ]
    )

    assert len(records) == 1
    assert records[0].filename == "dlc_test_ver01.tar.bz2"
    assert records[0].sha1 == "abc123"


def test_records_from_payload_joins_relative_urls() -> None:
    records = records_from_payload({"items": [{"file": "dlc_test_ver01.tar.bz2"}]})

    assert (
        records[0].url
        == "https://chroniclingamerica.loc.gov/data/ocr/dlc_test_ver01.tar.bz2"
    )


def test_records_from_html_accepts_data_ocr_index_rows() -> None:
    records = records_from_html(
        """
        <table>
          <tr><td><a href="../">Parent Directory</a></td><td></td><td></td></tr>
          <tr>
            <td><a href="dlc_test_ver01.tar.bz2">dlc_test_ver01.tar.bz2</a></td>
            <td>2024-03-18</td>
            <td>701.7 MB</td>
          </tr>
        </table>
        """,
        "https://chroniclingamerica.loc.gov/data/ocr/",
    )

    assert len(records) == 1
    assert records[0].filename == "dlc_test_ver01.tar.bz2"
    assert records[0].batch == "dlc_test_ver01"
    assert records[0].created == "2024-03-18"
    assert records[0].size == "701.7 MB"
    assert records[0].url == (
        "https://chroniclingamerica.loc.gov/data/ocr/dlc_test_ver01.tar.bz2"
    )
