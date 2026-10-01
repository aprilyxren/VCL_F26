import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from discover_place_candidates import pattern_hits  # noqa: E402


def observed(text: str) -> list[str]:
    return [hit[2] for hit in pattern_hits(text)]


def test_hundreds_and_islands():
    assert observed("sent to Martins Hundred and Mulberry Island") == ["Martins Hundred", "Mulberry Island"]


def test_leading_stopwords_are_trimmed():
    assert observed("vnto the said Smythes Hundred") == ["Smythes Hundred"]


def test_prose_and_numbers_are_not_places():
    assert observed("The towne was taken") == []
    assert observed("Three hundred men") == []


def test_prefix_forms():
    assert observed("the Riuer of Thames") == ["Riuer of Thames"]
    assert observed("came to Cape Marchant") == ["Cape Marchant"]
