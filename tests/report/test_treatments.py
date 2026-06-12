"""Treatment catalog: shipped data validates, lookup matches and ranks correctly."""

import pytest

from haid.report import SYMPTOM_KEYS, load_catalog
from haid.report.treatments import Catalog, Treatment, _validate


def test_shipped_catalog_loads_and_validates():
    cat = load_catalog()
    assert cat.version
    assert cat.treatments
    for t in cat.treatments:
        assert set(t.symptoms) <= SYMPTOM_KEYS
        assert t.last_verified


def test_match_ranks_by_overlap_then_maturity():
    a = Treatment(id="a", title="", symptoms=["rereads.cross_session"],
                  treatment="", mechanism="", applies_to=[], maturity="emerging",
                  sources=[{"title": "t", "url": "u", "date": "d"}])
    b = Treatment(id="b", title="",
                  symptoms=["rereads.cross_session", "retries.error_ignored"],
                  treatment="", mechanism="", applies_to=[], maturity="emerging",
                  sources=[{"title": "t", "url": "u", "date": "d"}])
    c = Treatment(id="c", title="", symptoms=["rereads.cross_session"],
                  treatment="", mechanism="", applies_to=[], maturity="official",
                  sources=[{"title": "t", "url": "u", "date": "d"}])
    cat = Catalog("v", "d", [a, b, c])
    got = cat.match(["rereads.cross_session", "retries.error_ignored"])
    assert [t.id for t in got] == ["b", "c", "a"]   # overlap first, then maturity


def test_match_unknown_symptom_raises():
    cat = load_catalog()
    with pytest.raises(KeyError, match="unknown symptom"):
        cat.match(["sounds.bad"])


def test_validate_rejects_bad_entries():
    base = {"id": "x", "title": "", "symptoms": ["rereads.cross_session"],
            "treatment": "", "mechanism": "", "applies_to": [],
            "maturity": "official", "sources": [], "last_verified": "2026-06-09"}
    with pytest.raises(ValueError, match="requires sources"):
        _validate(dict(base))                      # official w/o sources
    with pytest.raises(ValueError, match="unknown symptom"):
        _validate({**base, "symptoms": ["nope"], "maturity": "validated-in-house"})
    with pytest.raises(ValueError, match="maturity"):
        _validate({**base, "maturity": "vibes"})
