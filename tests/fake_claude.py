"""Stand-in for the `claude` CLI used by tests. Behaviour is chosen by FAKE_CLAUDE_MODE."""
import json
import os
import sys
import time

mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
if os.environ.get("FAKE_CLAUDE_ARGS"):
    with open(os.environ["FAKE_CLAUDE_ARGS"], "w") as fh:
        json.dump(sys.argv[1:], fh)
stdin = sys.stdin.read()
if os.environ.get("FAKE_CLAUDE_STDIN"):
    with open(os.environ["FAKE_CLAUDE_STDIN"], "w") as fh:
        fh.write(stdin)

if mode == "sleep":
    time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "10")))
    mode = "ok"

outputs = {
    "ok": {"type": "result", "is_error": False, "result": "{\"a\":1}", "structured_output": {"a": 1},
           "usage": {"input_tokens": 5, "output_tokens": 3}},
    "text": {"type": "result", "is_error": False, "result": "halo juga", "usage": {"input_tokens": 1, "output_tokens": 2}},
    "ratelimit": {"type": "result", "is_error": True, "api_error_status": 429,
                  "result": "Claude usage limit reached. Your limit will reset at 5pm"},
    "error": {"type": "result", "is_error": True, "api_error_status": 500, "result": "boom"},
    "login": {"type": "result", "is_error": True, "api_error_status": 401, "result": "Invalid API key · Please run /login"},
    "badjson": {"type": "result", "is_error": False, "result": "bukan json", "structured_output": None},
}
print(json.dumps(outputs[mode]))
