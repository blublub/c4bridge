# Voice prompt templates

Each folder has two files:
- `house.txt`: the rules for the LLM, with the placeholders `{rooms}`, `{devices}` and `{genres}`.
- `examples.txt`: example sentences and the tool call each one should produce.

| Folder | Language | Status |
|---|---|---|
| `de/` | German | Live prompt of the development house. With gemma4:12b, `tools/t_dialog.py` got 75 of 75 sentences right, about 0.7 s median |
| `en/` | English | Same as the built-in English default (`c4bridge/voice_en.py`). With gemma4:12b, `tools/t_dialog_en.py` got 26 of 27 right, about 0.5 s median |

How to use them:
- Paste both files into Admin › Voice control › LLM.
- Or test them without changing the live configuration:
  - `PROMPT_DIR=tools/prompts/de python tools/t_dialog.py`
  - `python tools/t_dialog_en.py`

Room and device names in the examples are illustrations. Adapt them to your own project. Tool values stay as the
tools define them, for example `strength="klein"`, `"mittel"` or `"gross"`. See "Languages" in the main README.
