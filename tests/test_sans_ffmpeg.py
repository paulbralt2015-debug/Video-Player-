"""Garde-fou : l'application ne lance jamais d'exécutable ffmpeg (ni aucun sous-processus)."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "twinsplayer"


def test_aucun_sous_processus_ni_ffmpeg_dans_le_code():
    bad = re.compile(r"subprocess|os\.system|os\.popen|ffmpeg|Popen", re.I)
    hits = [f"{p.name}:{i}: {line.strip()}" for p in SRC.glob("*.py")
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if bad.search(line)]
    assert not hits, "\n".join(hits)


def test_aucun_html():
    assert not list(SRC.rglob("*.html")) and not list(SRC.rglob("*.js"))
