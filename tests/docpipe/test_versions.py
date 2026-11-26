from agentorium.docpipe.models import Block
from agentorium.docpipe.versions import assign_uids


def blocks(*texts, uids=None):
    return [Block(id=f"b{i:05d}", uid=(uids[i] if uids else ""), ordinal=i, section_id="s0000", type="paragraph", text=t)
            for i, t in enumerate(texts)]


def test_first_version_gets_fresh_unique_uids():
    new = blocks("a", "b", "c")
    assign_uids(new, None)
    assert all(b.uid for b in new)
    assert len({b.uid for b in new}) == 3


def test_unchanged_blocks_keep_uid():
    prev = blocks("satu", "dua", "tiga", uids=["u1", "u2", "u3"])
    new = blocks("satu", "dua", "tiga")
    assign_uids(new, prev)
    assert [b.uid for b in new] == ["u1", "u2", "u3"]


def test_edited_block_keeps_uid_when_similar():
    prev = blocks("Pendahuluan", "Penjualan naik 12% dibanding Agustus.", "Penutup", uids=["u1", "u2", "u3"])
    new = blocks("Pendahuluan", "Penjualan naik 13% dibanding Agustus.", "Penutup")
    assign_uids(new, prev)
    assert [b.uid for b in new] == ["u1", "u2", "u3"]


def test_rewritten_block_gets_new_uid():
    prev = blocks("Pendahuluan", "Penjualan naik 12% dibanding Agustus.", "Penutup", uids=["u1", "u2", "u3"])
    new = blocks("Pendahuluan", "Tim baru bergabung pada bulan ini.", "Penutup")
    assign_uids(new, prev)
    assert new[0].uid == "u1" and new[2].uid == "u3"
    assert new[1].uid not in {"u1", "u2", "u3"} and new[1].uid


def test_inserted_block_gets_new_uid_and_neighbours_keep_theirs():
    prev = blocks("a satu", "b dua", uids=["u1", "u2"])
    new = blocks("a satu", "sisipan baru sekali", "b dua")
    assign_uids(new, prev)
    assert new[0].uid == "u1" and new[2].uid == "u2"
    assert new[1].uid not in {"u1", "u2"}


def test_reordered_duplicate_texts_do_not_share_uid():
    prev = blocks("sama", "sama", "lain", uids=["u1", "u2", "u3"])
    new = blocks("lain", "sama", "sama", "sama")
    assign_uids(new, prev)
    uids = [b.uid for b in new]
    assert len(set(uids)) == 4
