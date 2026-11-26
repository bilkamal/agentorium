import importlib.util
from pathlib import Path

from agentorium.docpipe.models import Block, Instruction

spec = importlib.util.spec_from_file_location("eval_revision", Path(__file__).parents[2] / "scripts" / "eval_revision.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def blk(i, text, uid=None, type_="paragraph"):
    return Block(id=f"b{i:05d}", uid=uid or f"u{i}", section_id="s0", ordinal=i, type=type_, text=text,
                 table_index=None, row=None, col=None)


def test_score_revision_counts_fixed_words_and_kept_numbers():
    old = [blk(0, "Laporan ini menganalisa kinerja."), blk(1, "Sebanyak 120 responden."), blk(2, "Resiko utama.")]
    new = [blk(0, "Laporan ini menganalisis kinerja."), blk(1, "Sebanyak 120 responden."), blk(2, "Resiko utama.")]
    r = ev.score_revision(old, new, bad_words=["menganalisa", "Resiko"], touched_uids={"u0"})
    assert r["fixed_words"] == 1 and r["bad_words"] == 2 and r["still_wrong"] == ["Resiko"]
    assert r["untouched_changed"] == [] and r["numbers_lost"] == [] and r["same_structure"]


def test_score_revision_flags_lost_numbers_and_untouched_edits():
    old = [blk(0, "Anggaran Rp 450 juta."), blk(1, "Tanggal 12 Oktober.")]
    new = [blk(0, "Anggaran juta."), blk(1, "Tanggal 13 Oktober.")]
    r = ev.score_revision(old, new, bad_words=[], touched_uids={"u0"})
    assert r["numbers_lost"] and r["untouched_changed"] == ["u1"]


def test_score_followup():
    def ins(text, status):
        return Instruction(id=text, job_id="j", ordinal=0, source="notes", author="", text=text, quote="",
                           block_ids=[], candidate_block_ids=[], status=status, response="")
    r = ev.score_followup([ins("Lampiran foto", "needs_human"), ins("Anggaran", "done")], unlocatable="Lampiran")
    assert r == {"instructions": 2, "done": 1, "not_applicable": 0, "needs_human": 1, "unlocatable_not_done": True}
