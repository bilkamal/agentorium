# Contributing to Agentorium

Thanks for helping! Good first contributions:

- **Adapters** for agent frameworks (LangGraph, CrewAI, AutoGen, OpenAI Agents SDK, Claude Agent SDK…)
  that translate the framework's callbacks into Agentorium events. Put them in `sdk/adapters/` with
  a runnable example in `examples/`.
- **Office furniture, activities and themes** in `web/scene.js` (everything is drawn with plain
  canvas calls — no build step).
- **Translations**: add a language block to `I18N` in `web/app.js` and `MOCK_TEXT` in `agentorium/llm.py`.
- **Providers**: `agentorium/llm.py` has `anthropic`, `openai`-compatible and `mock`.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
./run.sh --reload          # http://127.0.0.1:8000, mock agents, no API keys needed
```

- Python: keep modules small and typed; comments only where the logic is not obvious.
- JavaScript: vanilla ES2020, no frameworks, no bundler; `node --check web/*.js` must pass.
- Run `python examples/external_workflow.py` against a running server to check the event protocol.
- Never commit `.env` or `data/` (they contain API keys and user sessions).

## Pull requests

One topic per PR, with a short description and, for UI changes, a screenshot or GIF.
