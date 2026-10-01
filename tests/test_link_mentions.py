import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flag_page_roles import index_ratio, page_roles  # noqa: E402
from link_mentions import match_key, person_index, resolve_person  # noqa: E402

DICTIONARY = {
    "people": {
        "John Smith": {"aliases": ["Captaine John Smith"], "source": "list_of_records"},
        "Francis Carter": {"aliases": ["Francis Carter"], "source": "list_of_records"},
        "William Cavendish, Lord Cavendish": {"aliases": ["Lo Cauendish", "Lord Cauendish"], "source": "curated"},
    },
    "excluded": {"John Smith": "Not reliably Captain John Smith."},
}


def resolve(observed, name):
    index, excluded = person_index(DICTIONARY)
    return resolve_person({"observed_span": observed, "name_string": name}, index, excluded)


def test_spelling_variants_and_trailing_junk_match():
    assert match_key("Frauncis Carter") == match_key("Francis Carter")
    assert match_key("Lord Cauendish. The") == match_key("Lord Cauendish")


def test_titled_span_links_but_excluded_bare_name_does_not():
    assert resolve("Captaine John Smith", "John Smith")[0] == "John Smith"
    assert resolve("m' Iohn Smith", "Iohn Smith")[1] == "excluded"


def test_unknown_name_is_unresolved():
    assert resolve("m' Abraham Nobody", "Abraham Nobody")[1] == "unresolved"


def test_index_pages_are_the_trailing_run_only():
    pages = [
        {"page_id": "V03-P0001", "volume": 3, "text": "JUNE 22, 1620 337\nAndrew Willmer____., 25.\nClement Willmer__., 25.\n"},
        {"page_id": "V03-P0002", "volume": 3, "text": "A letter of business\nconcerning the colony\nand its supplies\n"},
        {"page_id": "V03-P0003", "volume": 3, "text": "Index\nAbbot, George, 81, 320.\nAccounts, I, 83.\n"},
        {"page_id": "V03-P0004", "volume": 3, "text": ""},
    ]
    assert index_ratio(pages[0]["text"]) >= 0.3  # a shareholder list looks index-like...
    roles = page_roles(pages)
    assert roles["V03-P0001"] == "record"        # ...but is not in the trailing run
    assert roles["V03-P0003"] == roles["V03-P0004"] == "index"
