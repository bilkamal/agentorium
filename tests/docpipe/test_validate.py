from agentorium.docpipe.models import Block
from agentorium.docpipe.validate import normalize_ws, validate_fact, validate_finding

B = {"b00001": Block(id="b00001", uid="u", ordinal=1, section_id="s0000", type="paragraph",
                     text="Penjualan  bulan September naik 12 persen, menurut “Laporan Toko”.")}


def raw(**kw):
    base = dict(block_id="b00001", category="language", severity="low", quote="naik 12 persen",
                description="Kurang tepat.", suggestion="meningkat 12 persen", confidence=0.8)
    return {**base, **kw}


def test_valid_finding_passes():
    f, reason = validate_finding(raw(), B)
    assert reason is None
    assert f["block_id"] == "b00001" and f["category"] == "language" and f["confidence"] == 0.8
    assert f["suggestion"] == "meningkat 12 persen"


def test_unknown_block_rejected():
    f, reason = validate_finding(raw(block_id="b99999"), B)
    assert f is None and "block" in reason


def test_quote_not_in_block_rejected():
    f, reason = validate_finding(raw(quote="turun 5 persen"), B)
    assert f is None and "quote" in reason
    assert validate_finding(raw(quote="  "), B)[0] is None
    assert validate_finding(raw(quote="NAIK 12 PERSEN"), B)[0] is None  # case-sensitive


def test_quote_matches_across_whitespace_and_curly_quotes():
    assert validate_finding(raw(quote='Penjualan bulan September naik'), B)[0] is not None
    assert validate_finding(raw(quote='menurut "Laporan Toko"'), B)[0] is not None


def test_bad_category_rejected():
    assert validate_finding(raw(category="style"), B)[1]
    assert validate_finding(raw(severity="critical"), B)[1]


def test_empty_description_rejected():
    assert validate_finding(raw(description=" "), B)[0] is None


def test_confidence_clamped():
    assert validate_finding(raw(confidence=3), B)[0]["confidence"] == 1.0
    assert validate_finding(raw(confidence=-1), B)[0]["confidence"] == 0.0
    assert validate_finding(raw(confidence="x"), B)[0]["confidence"] == 0.5
    f = dict(raw())
    del f["confidence"]
    assert validate_finding(f, B)[0]["confidence"] == 0.5


def test_non_dict_rejected():
    assert validate_finding("nope", B) == (None, "not an object")


def test_normalize_ws():
    assert normalize_ws(" a\t\n b c ‘d’ ") == "a b c 'd'"


def test_fact_validation():
    ok = validate_fact(dict(block_id="b00001", kind="number", key="kenaikan penjualan", value="12",
                            unit="persen", quote="naik 12 persen"), B)
    assert ok == dict(block_id="b00001", kind="number", key="kenaikan penjualan", value="12", unit="persen",
                      quote="naik 12 persen")
    assert validate_fact(dict(block_id="b00001", kind="mood", key="k", value="v", quote="naik"), B) is None
    assert validate_fact(dict(block_id="b00001", kind="number", key="k", value="v", quote="tidak ada"), B) is None
    assert validate_fact(dict(block_id="b00001", kind="number", key="", value="v", quote="naik"), B) is None
