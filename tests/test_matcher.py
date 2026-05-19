from civil_war_search.matcher import PurePythonAhoMatcher, normalize_text


def test_normalize_text_casefolds_punctuation_and_spacing() -> None:
    assert normalize_text("  Fort-Sumter,\nS.C.!  ") == "fort sumter s c"


def test_matcher_finds_phrases_and_rejects_substrings() -> None:
    keywords = ["war", "fort sumter"]
    matcher = PurePythonAhoMatcher(keywords)

    matches = matcher.find(normalize_text("The Fort-Sumter report. A war note."))
    assert sorted(matches) == ["fort sumter", "war"]

    no_substring = matcher.find(normalize_text("This forward dispatch is warm."))
    assert no_substring == {}
