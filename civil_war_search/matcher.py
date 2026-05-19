from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import re
from typing import Iterable


SPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Normalize OCR and keywords into a reproducible search form."""
    chars: list[str] = []
    for char in value.casefold():
        chars.append(char if char.isalnum() else " ")
    return SPACE_RE.sub(" ", "".join(chars)).strip()


def load_keywords(path: str) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            normalized = normalize_text(line)
            if normalized and normalized not in seen:
                keywords.append(normalized)
                seen.add(normalized)
    if not keywords:
        raise ValueError(f"no keywords found in {path}")
    return keywords


class KeywordMatcher:
    def find(self, normalized_text: str) -> dict[str, list[int]]:
        raise NotImplementedError


class PyAhoMatcher(KeywordMatcher):
    def __init__(self, keywords: Iterable[str]) -> None:
        import ahocorasick  # type: ignore[import-not-found]

        self._automaton = ahocorasick.Automaton()
        for keyword in keywords:
            self._automaton.add_word(f" {keyword} ", keyword)
        self._automaton.make_automaton()

    def find(self, normalized_text: str) -> dict[str, list[int]]:
        matches: dict[str, list[int]] = {}
        searchable = f" {normalized_text} "
        for end_index, keyword in self._automaton.iter(searchable):
            start_index = end_index - len(keyword) - 1
            matches.setdefault(keyword, []).append(max(0, start_index))
        return matches


@dataclass(slots=True)
class _TrieNode:
    next: dict[str, int] = field(default_factory=dict)
    fail: int = 0
    outputs: list[str] = field(default_factory=list)


class PurePythonAhoMatcher(KeywordMatcher):
    def __init__(self, keywords: Iterable[str]) -> None:
        self._nodes = [_TrieNode()]
        for keyword in keywords:
            pattern = f" {keyword} "
            node_index = 0
            for char in pattern:
                child = self._nodes[node_index].next.get(char)
                if child is None:
                    child = self._new_node()
                    self._nodes[node_index].next[char] = child
                node_index = child
            self._nodes[node_index].outputs.append(keyword)
        self._build_failure_links()

    def _new_node(self) -> int:
        self._nodes.append(_TrieNode())
        return len(self._nodes) - 1

    def _build_failure_links(self) -> None:
        queue: deque[int] = deque()
        for child in self._nodes[0].next.values():
            self._nodes[child].fail = 0
            queue.append(child)

        while queue:
            current = queue.popleft()
            for char, child in self._nodes[current].next.items():
                fail = self._nodes[current].fail
                while fail and char not in self._nodes[fail].next:
                    fail = self._nodes[fail].fail
                self._nodes[child].fail = self._nodes[fail].next.get(char, 0)
                self._nodes[child].outputs.extend(
                    self._nodes[self._nodes[child].fail].outputs
                )
                queue.append(child)

    def find(self, normalized_text: str) -> dict[str, list[int]]:
        matches: dict[str, list[int]] = {}
        searchable = f" {normalized_text} "
        node_index = 0

        for index, char in enumerate(searchable):
            while node_index and char not in self._nodes[node_index].next:
                node_index = self._nodes[node_index].fail
            node_index = self._nodes[node_index].next.get(char, 0)
            for keyword in self._nodes[node_index].outputs:
                start_index = index - len(keyword) - 1
                matches.setdefault(keyword, []).append(max(0, start_index))
        return matches


def build_matcher(keywords: Iterable[str]) -> KeywordMatcher:
    keyword_list = list(keywords)
    try:
        return PyAhoMatcher(keyword_list)
    except ImportError:
        return PurePythonAhoMatcher(keyword_list)
