"""Download-Pakete für eine GitHub-Release bauen (c4bridge/gpu_packages.py lädt sie).

Aufruf:  python tools/make_release.py <Quell-Installation> <Ausgabeordner>
Quelle = ein Projektordner, in dem Whisper (Vulkan) und Qwen3-TTS laufen (bin/whisper-vulkan, bin/tts/qwen3).
Erzeugt:
  whisper-vulkan-win-x64.zip   whisper-server.exe (+ Lizenz, Version)                 → bin/whisper-vulkan/
  qwen3-engine-win-x64.zip     qwen3_tts_gguf (nur inference + llama.cpp-DLLs, Lizenzen) → bin/tts/qwen3/
  qwen3-tts-0.6b-base.zip      exportiertes Qwen3-TTS-12Hz-0.6B-Base (GGUF/ONNX, Lizenz)  → bin/tts/qwen3/model-base-small/
  SHA256SUMS.txt
Danach: gh release create v1.0.0 <Ausgabeordner>/* (Tag = gpu_packages.RELEASE_TAG).
"""
import hashlib
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
MIT_LLAMA = """MIT License

Copyright (c) 2023-2026 The ggml authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
MODEL_README = """Qwen3-TTS-12Hz-0.6B-Base, exported for the Qwen3-TTS-GGUF engine (talker/predictor as GGUF, codec/decoder/
speaker encoder as ONNX) with the export scripts of https://github.com/HaujetZhao (MIT).
Original model: https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base – Apache License 2.0 (see LICENSE-Qwen3-TTS.txt).
Converted and quantised by the c4bridge project; no other changes.
"""


def add_tree(z: zipfile.ZipFile, src: Path, arc: str, skip=lambda p: False) -> None:
    for p in sorted(src.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and not skip(p):
            z.write(p, f"{arc}/{p.relative_to(src).as_posix()}" if arc else p.relative_to(src).as_posix())


def main():
    src, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    wv, q = src / "bin" / "whisper-vulkan", src / "bin" / "tts" / "qwen3"
    apache = (HERE / "c4bridge" / "static" / "vendor" / "hls.js.LICENSE.txt").read_text(encoding="utf-8")
    apache = apache[apache.index("Apache License"):]                       # nur der Lizenztext

    with zipfile.ZipFile(out / "whisper-vulkan-win-x64.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in ("whisper-server.exe", "LICENSE-whisper.cpp.txt", "VERSION.txt"):
            z.write(wv / f, f)

    inf = q / "qwen3_tts_gguf" / "inference"
    with zipfile.ZipFile(out / "qwen3-engine-win-x64.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.write(q / "qwen3_tts_gguf" / "__init__.py", "qwen3_tts_gguf/__init__.py")
        add_tree(z, inf, "qwen3_tts_gguf/inference",
                 skip=lambda p: p.suffix == ".exe" or p.name.endswith("-impl.dll"))   # nur die Bibliotheken
        z.write(q / "LICENSE", "LICENSE-Qwen3-TTS-GGUF.txt")
        z.writestr("LICENSE-llama.cpp.txt", MIT_LLAMA)

    with zipfile.ZipFile(out / "qwen3-tts-0.6b-base.zip", "w", zipfile.ZIP_DEFLATED) as z:
        add_tree(z, q / "model-base-small", "")
        z.writestr("LICENSE-Qwen3-TTS.txt", apache)
        z.writestr("README.txt", MODEL_README)

    lines = []
    for f in sorted(out.glob("*.zip")):
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {f.name}")
        print(f"{f.name}: {f.stat().st_size / 1e6:.0f} MB")
    (out / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
