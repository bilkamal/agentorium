from agentorium.docpipe.parse import parse_docx

from .eval_doc import make_eval_docx


def test_eval_doc_contains_all_seeded_errors(tmp_path):
    path = tmp_path / "eval.docx"
    seeded = make_eval_docx(path)
    assert len(seeded) >= 20
    assert {e.category for e in seeded} == {"language", "consistency", "structure", "substance"}
    parsed = parse_docx(path)
    heading_of = {s.id: s.heading for s in parsed.sections}
    for e in seeded:
        for needle in [e.block_text_contains] + ([e.alt_contains] if e.alt_contains else []):
            hits = [b for b in parsed.blocks if needle in b.text]
            assert len(hits) == 1, (needle, len(hits))
        hit = next(b for b in parsed.blocks if e.block_text_contains in b.text)
        assert heading_of[hit.section_id] == e.section_heading


def test_score_matches_by_block_and_category():
    from agentorium.docpipe.models import Block, Finding
    from scripts.eval_review import score

    from .eval_doc import SeededError

    blocks = [Block(id="b1", uid="", ordinal=0, section_id="s", type="paragraph", text="Resiko utama naik"),
              Block(id="b2", uid="", ordinal=1, section_id="s", type="paragraph", text="survei 120 responden"),
              Block(id="b3", uid="", ordinal=2, section_id="s", type="paragraph", text="diikuti 125 responden"),
              Block(id="b4", uid="", ordinal=3, section_id="s", type="paragraph", text="kalimat lain")]
    seeded = [SeededError("s", "Resiko utama", "language"),
              SeededError("s", "120 responden", "consistency", alt_contains="125 responden"),
              SeededError("s", "kalimat lain", "substance")]

    def f(block, cat, other=None):
        return Finding(id="x", job_id="j", task_id=None, version_id="v", block_id=block, other_block_id=other,
                       category=cat, severity="low", quote="q", description="d", suggestion="", confidence=1,
                       status="valid", validator_note=None)

    r = score(seeded, [f("b1", "language"), f("b3", "consistency"), f("b4", "language"), f("b1", "language")], blocks)
    assert r["found"] == 2 and r["seeded"] == 3 and abs(r["recall"] - 2 / 3) < 1e-9
    assert r["missed"] == ["kalimat lain"]
    assert r["matched_findings"] == 3 and r["valid_findings"] == 4 and r["precision"] == 0.75
    assert r["by_category"]["consistency"] == {"seeded": 1, "found": 1}
