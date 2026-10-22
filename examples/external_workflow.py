"""Minimal external workflow that uses Agentorium purely as a visualiser.

Run the server (./run.sh), then:  python examples/external_workflow.py
Open the UI, pick the session "Example: external workflow" and watch the office.
Replace the `time.sleep` calls with your real LangGraph / CrewAI / AutoGen / custom agent calls.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sdk" / "python"))
from agentorium import Office  # noqa: E402

office = Office("http://127.0.0.1:8000")
s = office.session("Example: external workflow")
s.register("planner", "Planner", role="supervisor", color="#1a9f5c", hair="#6b3fa0")
s.register("researcher", "Researcher", role="worker", color="#4f6df5", skills="web research")
s.register("writer", "Writer", role="worker", color="#d98c00", hair="#f2c14e", skills="copywriting")
print("Open the UI and select the session:", s.id)

s.stage("planning"); s.busy(True)
s.status("planner", "planning")
time.sleep(4)   # <- your planning call
s.plan("Write a short market brief", [
    {"id": 1, "title": "Collect sources", "assignee": "researcher", "instructions": "Find 3 sources", "deliverable": "Source list"},
    {"id": 2, "title": "Draft the brief", "assignee": "writer", "instructions": "Write 200 words", "deliverable": "Brief", "depends_on": [1]},
])
s.status("planner", "idle")

s.stage("executing")
for step_id, agent in ((1, "researcher"), (2, "writer")):
    s.flow("planner", agent, f"step {step_id}")
    s.step(step_id, "running", assignee=agent)
    s.status(agent, f"working on step {step_id}")
    time.sleep(6)   # <- your agent call
    s.output(agent, step_id, f"(example output of step {step_id})")
    s.flow(agent, "planner", "result")
    s.status(agent, "idle"); s.status("planner", f"reviewing step {step_id}")
    time.sleep(3)   # <- your review call
    s.review(step_id, "approve", score=9, feedback="Looks good")
    s.step(step_id, "done", assignee=agent)
    s.status("planner", "idle")

s.flow("planner", "user", "delivery")
s.say("planner", "All done — here is the brief:\n\n**Market brief** (example output)")
s.stage("done"); s.busy(False)

print("Waiting for user messages (Ctrl+C to stop)…")
try:
    for message in s.user_messages():
        s.status("planner", "thinking"); time.sleep(2)
        s.say("planner", f"You said: {message}")
        s.status("planner", "idle")
except KeyboardInterrupt:
    pass
