from __future__ import annotations

import json

from civil_war_search.keyword_groups import (
    filter_matches_by_groups,
    load_keyword_groups,
)


def test_keyword_groups_gate_broad_terms_by_anchor_group(tmp_path) -> None:
    config = tmp_path / "groups.json"
    config.write_text(
        json.dumps(
            {
                "groups": [
                    {
                        "name": "anchors",
                        "keywords": ["criminal court", "guard house"],
                    },
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
    plan = load_keyword_groups(str(config))

    filtered, matched_groups = filter_matches_by_groups({"fine": [1]}, plan)
    assert filtered == {}
    assert matched_groups == {}

    filtered, matched_groups = filter_matches_by_groups(
        {"fine": [1], "criminal court": [10]},
        plan,
    )
    assert filtered == {"criminal court": [10], "fine": [1]}
    assert matched_groups == {
        "anchors": ["criminal court"],
        "broad": ["fine"],
    }
