import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import person_dictionary  # noqa: E402

DICTIONARY = Path(__file__).resolve().parents[1] / "data/authority/list_of_records_person_alias_dictionary.json"


def test_round_trip_is_stable(tmp_path):
    copy = tmp_path / "people.json"
    copy.write_text(DICTIONARY.read_text(encoding="utf-8"), encoding="utf-8")
    person_dictionary.save(copy, person_dictionary.load(copy))
    assert copy.read_text(encoding="utf-8") == DICTIONARY.read_text(encoding="utf-8")


def test_every_person_has_alias_list_and_source():
    data = person_dictionary.load(DICTIONARY)
    for name, entry in data["people"].items():
        assert isinstance(entry["aliases"], list), name
        assert entry["source"] in {"list_of_records", "curated", "territorial_title_rule"}, name


def test_excluded_names_are_not_aliases():
    data = person_dictionary.load(DICTIONARY)
    aliases = {a.casefold() for entry in data["people"].values() for a in entry["aliases"]}
    assert not {name.casefold() for name in data["excluded"]} & aliases


def test_rejects_old_flat_format(tmp_path):
    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({"Samuel Argall": ["Captain Argall"]}), encoding="utf-8")
    try:
        person_dictionary.load(flat)
    except ValueError:
        return
    raise AssertionError("flat format should be rejected")
