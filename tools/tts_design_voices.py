"""c4: Qwen3-TTS VoiceDesign – zwei neue Stimmen als Vorlage für Qwen3 erzeugen (je 2 Varianten, CPU)."""
import os, sys, time, random
from pathlib import Path
import numpy as np, soundfile as sf, torch

QWEN = os.environ.get("QWEN_DIR", str(Path(__file__).resolve().parent.parent / "bin" / "tts" / "qwen3"))
sys.path.insert(0, str(Path(QWEN) / "Qwen3-TTS-main"))
from qwen_tts import Qwen3TTSModel

MODEL = os.environ.get("QWEN_DESIGN_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign")
OUT = os.environ.get("OUT_DIR", "voices_out")

TEXT = ("Alles klar, ich habe das Licht im Wohnzimmer eingeschaltet. Draußen sind es gerade elf Grad, "
        "und am Nachmittag soll es leicht regnen. Soll ich die Rollläden im Garten schon herunterfahren?")

VOICES = {
    "abenteuer": """gender: Female.
age: Early thirties.
pitch: Medium-low female pitch, slightly husky.
speed: Moderate, confident pace with crisp phrasing.
volume: Clear conversational volume.
clarity: Very clear, precise articulation.
accent: Standard High German, native speaker, with a subtle touch of British elegance.
texture: Smooth with a light smoky warmth.
emotion: Cool, composed, quietly amused.
tone: Self-assured, adventurous, a little teasing.
personality: Fearless explorer, witty, elegant and in control.""",
    "ruhig": """gender: Female.
age: Around forty.
pitch: Medium female pitch, very stable.
speed: Calm, unhurried pace with natural pauses.
volume: Soft but clearly audible.
clarity: Clear, gentle articulation.
accent: Standard High German, native speaker.
texture: Warm, soft and round.
emotion: Relaxed and friendly.
tone: Calm, reassuring, pleasant.
personality: Kind, patient and attentive.""",
}


def main():
    torch.set_num_threads(max(1, (torch.get_num_threads() or 8)))
    t = time.time()
    tts = Qwen3TTSModel.from_pretrained(MODEL, device_map="cpu", dtype=torch.float32)
    print(f"geladen in {time.time() - t:.0f}s", flush=True)
    for name, instruct in VOICES.items():
        for seed in (47, 11):
            random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
            t = time.time()
            wavs, sr = tts.generate_voice_design(text=TEXT, instruct=instruct, language="German",
                                                 temperature=0.8, subtalker_temperature=0.8)
            p = f"{OUT}\\design_{name}_{seed}.wav"
            sf.write(p, wavs[0], sr)
            print(f"{name} seed {seed}: {len(wavs[0]) / sr:.1f}s Audio in {time.time() - t:.0f}s -> {p}", flush=True)


if __name__ == "__main__":
    main()
