from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .matcher import load_keywords, normalize_text


@dataclass(frozen=True, slots=True)
class KeywordGroup:
    name: str
    keywords: tuple[str, ...]
    require_any: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KeywordPlan:
    keywords: tuple[str, ...]
    groups: tuple[KeywordGroup, ...] = ()

    @property
    def grouped(self) -> bool:
        return bool(self.groups)


def load_keyword_plan(
    keywords_path: str | None = None,
    keyword_groups_path: str | None = None,
) -> KeywordPlan:
    if keyword_groups_path:
        return load_keyword_groups(keyword_groups_path)
    if not keywords_path:
        raise ValueError("either keywords_path or keyword_groups_path is required")
    return KeywordPlan(keywords=tuple(load_keywords(keywords_path)))


def load_keyword_groups(path: str) -> KeywordPlan:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)

    raw_groups = payload.get("groups") if isinstance(payload, dict) else None
    if not isinstance(raw_groups, list):
        raise ValueError("keyword groups file must contain a 'groups' list")

    groups: list[KeywordGroup] = []
    all_keywords: list[str] = []
    seen_keywords: set[str] = set()
    seen_groups: set[str] = set()

    for index, raw_group in enumerate(raw_groups, start=1):
        if not isinstance(raw_group, dict):
            raise ValueError(f"group {index} must be an object")

        name = raw_group.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"group {index} is missing a name")
        name = name.strip()
        if name in seen_groups:
            raise ValueError(f"duplicate group name: {name}")
        seen_groups.add(name)

        raw_keywords = raw_group.get("keywords")
        if not isinstance(raw_keywords, list):
            raise ValueError(f"group {name} must contain a keywords list")

        keywords: list[str] = []
        for raw_keyword in raw_keywords:
            if not isinstance(raw_keyword, str):
                continue
            keyword = normalize_text(raw_keyword)
            if keyword:
                keywords.append(keyword)
                if keyword not in seen_keywords:
                    all_keywords.append(keyword)
                    seen_keywords.add(keyword)

        if not keywords:
            raise ValueError(f"group {name} has no usable keywords")

        raw_require_any = raw_group.get("require_any", [])
        if raw_require_any is None:
            raw_require_any = []
        if not isinstance(raw_require_any, list) or not all(
            isinstance(item, str) for item in raw_require_any
        ):
            raise ValueError(f"group {name} require_any must be a list of group names")

        groups.append(
            KeywordGroup(
                name=name,
                keywords=tuple(dict.fromkeys(keywords)),
                require_any=tuple(raw_require_any),
            )
        )

    group_names = {group.name for group in groups}
    for group in groups:
        missing = set(group.require_any) - group_names
        if missing:
            raise ValueError(
                f"group {group.name} requires unknown groups: {', '.join(sorted(missing))}"
            )

    return KeywordPlan(keywords=tuple(all_keywords), groups=tuple(groups))


def filter_matches_by_groups(
    matches: dict[str, list[int]],
    plan: KeywordPlan,
) -> tuple[dict[str, list[int]], dict[str, list[str]]]:
    if not plan.grouped:
        return matches, {}

    raw_group_hits: dict[str, list[str]] = {}
    for group in plan.groups:
        group_hits = [keyword for keyword in group.keywords if keyword in matches]
        if group_hits:
            raw_group_hits[group.name] = group_hits

    matched_groups: dict[str, list[str]] = {}
    filtered: dict[str, list[int]] = {}
    for group in plan.groups:
        group_hits = raw_group_hits.get(group.name, [])
        if not group_hits:
            continue
        if group.require_any and not any(
            required_group in raw_group_hits for required_group in group.require_any
        ):
            continue

        matched_groups[group.name] = sorted(group_hits)
        for keyword in group_hits:
            filtered[keyword] = matches[keyword]

    return filtered, matched_groups


def groups_as_dict(matched_groups: dict[str, list[str]]) -> dict[str, Any]:
    return {name: keywords for name, keywords in sorted(matched_groups.items())}
