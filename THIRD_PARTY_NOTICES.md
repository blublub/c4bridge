# Third-party notices

The c4bridge source code is licensed under the MIT License (see `LICENSE`). This file lists the third-party
components that c4bridge contains, installs or downloads, and their licences.

## Included in this repository

| Component | File(s) | Licence |
|---|---|---|
| hls.js 1.5.20 (© Dailymotion) | `c4bridge/static/vendor/hls.min.js` | Apache-2.0, full text in `c4bridge/static/vendor/hls.js.LICENSE.txt` |
| llama.cpp `tools/tts/tts.cpp` (© The ggml authors) | `tools/tts-loop.cpp` is derived from it | MIT |
| MediaMTX sample configuration | `tools/mediamtx.yml` | MIT ([bluenviron/mediamtx](https://github.com/bluenviron/mediamtx)) |
| Sample voices generated with Qwen3-TTS VoiceDesign (2 German, 2 English) | `data/tts/qwen_ref_*.wav` | Generated with an Apache-2.0 model. The audio is synthetic and is not a clone of a real person |

## Distributed in the release packages (downloaded by the admin)

Each package contains the licence texts of what it ships.

| Package | Contents | Licence |
|---|---|---|
| `whisper-vulkan-win-x64.zip` | `whisper-server.exe`, built from whisper.cpp (© The ggml authors) with Vulkan | MIT |
| `qwen3-engine-win-x64.zip` | Qwen3-TTS-GGUF inference engine (© HaujetZhao) and llama.cpp / ggml libraries (© The ggml authors); LLVM OpenMP runtime | MIT; OpenMP: Apache-2.0 with LLVM exception |
| `qwen3-tts-0.6b-base.zip` | Qwen3-TTS-12Hz-0.6B-Base (© Alibaba Qwen team), converted to GGUF/ONNX | Apache-2.0 |

## Python packages (installed by pip, not included)

| Package | Licence |
|---|---|
| pyControl4 | Apache-2.0 |
| FastAPI, pydantic, RapidFuzz, faster-whisper, CTranslate2, onnxruntime | MIT |
| uvicorn, Starlette, websockets, PyAV, SciPy | BSD-3-Clause |
| aiohttp | Apache-2.0 AND MIT |
| cryptography | Apache-2.0 OR BSD-3-Clause |
| NumPy | BSD-3-Clause (plus compatible licences) |
| openWakeWord (code) | Apache-2.0 |
| **piper-tts** (optional) | **GPL-3.0-or-later**, see the note below |
| Qwen3-TTS environment (`requirements-qwen.txt`): onnxruntime-directml, gguf, sounddevice | MIT |
| … tokenizers | Apache-2.0 |
| … soundfile, SciPy, NumPy | BSD-3-Clause (NumPy plus compatible licences) |

**Note on Piper:** c4bridge only imports `piper` at runtime when the Piper speech output is selected. Piper is
not included in this repository. The MIT licence of c4bridge is compatible with the GPL. If you distribute a
package that **bundles** piper-tts together with c4bridge, the combined distribution must follow GPL-3.0.

## Models and tools (downloaded separately, not included)

| Component | Licence | Note |
|---|---|---|
| openWakeWord pre-trained models (for example `hey_jarvis`) | **CC BY-NC-SA 4.0** | **Non-commercial use only.** Train your own wake word model for commercial use |
| Piper voices (rhasspy/piper-voices) | Varies per voice | `thorsten` is CC0. Some voices are non-commercial. Check each voice's `MODEL_CARD` |
| Whisper models (OpenAI) and the faster-whisper/ONNX conversions | MIT / Apache-2.0 | Check the licence of each converted model on Hugging Face |
| whisper.cpp / llama.cpp | MIT | |
| Qwen3-TTS (Qwen team) | Apache-2.0 | |
| Qwen3-TTS GGUF engine / export scripts (HaujetZhao) | MIT | |
| Ollama | MIT | |
| LLM weights (for example gemma4, qwen3.5) | Each model's own terms | See the model card. Weights are pulled by Ollama |
| MediaMTX | MIT | Downloaded by the setup wizard (pinned version, SHA-256 checked) |
| WinSW | MIT | |

## Trademarks

Control4 is a trademark of Snap One, LLC. BluOS, Bluesound, NAD, Qobuz and KODI are trademarks of their
respective owners. This project is not affiliated with or endorsed by any of them. It talks to the
Control4 Director through the same local API as pyControl4.
