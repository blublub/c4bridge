"""Test für den Wortschatz (c4bridge/wortschatz.py): Korrekturen, Klang, eingedeutschte Wake-Phrase, Hinweistext.
Nach jeder Änderung an wortschatz.py laufen lassen: .venv\\Scripts\\python.exe tools\\t_wortschatz.py"""
from pathlib import Path
import sys
sys.stdout.reconfigure(encoding="utf-8")   # Windows-Konsole (cp1252) kann „→“ nicht
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from c4bridge import wortschatz as w  # noqa: E402

ok = True


def check(label, got, want):
    global ok
    good = got == want
    ok &= good
    print(("OK   " if good else "FEHLER ") + f"{label}: {got!r}" + ("" if good else f"  (erwartet {want!r})"))


# Korrekturen + Klang: diese Hörfehler müssen zu Qobuz werden …
for wrong in ["über Kubos", "über Quobus", "auf Kobutz", "bei Gobus", "auf Bobus", "über Kuburz", "spiel über Quos"]:
    check(f"„{wrong}“", w.correct(wrong).split()[-1], "Qobuz")
# … diese Wörter dürfen NICHT angefasst werden
for keep in ["Kapuze", "Gabriel", "Kugeln", "Kabel", "Kreis", "Geist", "Kies", "Schreibtisch"]:
    check(f"„{keep}“ bleibt", w.correct(keep), keep)
check("TuneIn", w.correct("mach tune in an"), "mach TuneIn an")
# eingedeutschte Wake-Phrase
check("„Ja, das. Licht an“", w.strip_wake_deutsch("Ja, das. Licht an"), "Licht an")
check("„Ich habe das? Nein …“", w.strip_wake_deutsch("Ich habe das? Nein, das Licht"), "Nein, das Licht")
check("„Mach das Licht an“ ohne", w.strip_wake_deutsch("Mach das Licht an"), None)
# Hinweistext
h = w.build_hint("Hey Jarvis", "Schreibtischlampe", ["Wohnzimmer", "Bad oben"], ["Adele", "Sting"])
check("Hinweistext enthält alles", all(x in h for x in ("Hey Jarvis", "Schreibtischlampe", "Bad oben", "Qobuz", "Adele")), True)
# f) zuletzt gespielte Namen
import tempfile, pathlib  # noqa: E402
zf = pathlib.Path(tempfile.gettempdir()) / "c4_t_wortschatz_zuletzt.json"
zf.unlink(missing_ok=True)
w.merke(zf, ["Imagine Dragons", "Believer"])
w.merke(zf, ["Avril Lavigne", "Sk8er Boi (Live)", "30", ""])
w.merke(zf, ["imagine dragons", "Thunder"])
check("merke: neueste vorn, ohne Doppelte/Zusätze/Zahlen", w.zuletzt(zf),
      ["imagine dragons", "Thunder", "Avril Lavigne", "Sk8er Boi", "Believer"])
h2 = w.build_hint("Hey Jarvis", "", ["Wohnzimmer"], ["Adele"] * 200, w.zuletzt(zf))
check("Hinweistext: Zuletzt steht am Ende",
      h2.endswith("Zuletzt: imagine dragons, Thunder, Avril Lavigne, Sk8er Boi, Believer."), True)
check("Hinweistext bleibt begrenzt (< 700 Zeichen)", len(h2) < 700, True)
zf.unlink(missing_ok=True)
print("\nALLES OK" if ok else "\nFEHLER vorhanden")
