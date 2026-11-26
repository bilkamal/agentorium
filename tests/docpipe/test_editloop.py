import asyncio
from types import SimpleNamespace

from agentorium.docpipe.editloop import edit_loop


class Seq:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    async def complete_json(self, system, messages, schema, *, purpose=""):
        self.prompts.append(messages[-1]["content"])
        return self.replies.pop(0)


def blocks():
    return {"b1": SimpleNamespace(id="b1", text="Kami menganalisa data."),
            "b2": SimpleNamespace(id="b2", text="Resiko utama naik.")}


def test_approved_edits_are_not_resubmitted_or_duplicated():
    e1 = {"block_id": "b1", "new_text": "Kami menganalisis data.", "source_ids": ["f1"], "reason": "baku"}
    e2 = {"block_id": "b2", "new_text": "Risiko utama naik.", "source_ids": ["f2"], "reason": "baku"}
    e2b = {**e2, "reason": "baku, diperbaiki"}
    w = Seq([{"edits": [e1, e2], "skipped": []}, {"edits": [e1, e2b], "skipped": []}])
    s = Seq([{"verdicts": [{"index": 0, "verdict": "approve"}, {"index": 1, "verdict": "revise", "feedback": "x"}]},
             {"verdicts": [{"index": 0, "verdict": "approve"}]}])
    out = asyncio.run(edit_loop(wllm=w, sllm=s, worker_system="", user_for=lambda fb: "u", section_path="S",
                                blocks_by_id=blocks(), basis={"f1": "menganalisa", "f2": "Resiko"}, doc_texts=[],
                                purpose="t"))
    assert [(e["block_id"], e["reason"]) for e in out.approved] == [("b1", "baku"), ("b2", "baku, diperbaiki")]
    assert "b1" not in s.prompts[1]  # second verdict only sees the revised edit
