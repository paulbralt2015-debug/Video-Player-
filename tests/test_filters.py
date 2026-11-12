import re
import shutil
import subprocess

import pytest

from twinsplayer.filters import (AUDIO_MODES, VIDEO_STANDARDS, AudioSettings, build_audio,
                                 build_video, frame_size)


def raw(s: str) -> str:
    m = re.match(r"lavfi=graph=%(\d+)%(.*)$", s, re.S)
    assert m, s
    assert int(m.group(1)) == len(m.group(2).encode("utf-8")), "longueur %N% incorrecte"
    return m.group(2)


def test_stereo_sans_traitement_ne_met_aucun_filtre():
    plan = build_audio(AudioSettings())
    assert plan.af == "" and plan.channels == "auto-safe"


def test_stereo_avec_basses_et_salle():
    g = raw(build_audio(AudioSettings(bass_db=6, room=40)).af)
    assert "bass=g=6" in g and "aecho" in g and "pan" not in g


def test_curseur_salle_agit_enfin():
    # Régression : l'ancienne page HTML avait deux éléments id="rev" et la réverbération ne s'activait jamais.
    assert build_audio(AudioSettings(mode="pl", room=0)).af != build_audio(AudioSettings(mode="pl", room=60)).af


@pytest.mark.parametrize("mode", [m for m in AUDIO_MODES if m not in ("stereo", "wide")])
def test_modes_matriciels(mode):
    for out, channels in (("hp", "stereo"), ("51", "5.1")):
        plan = build_audio(AudioSettings(mode=mode, output=out))
        assert plan.channels == channels
        g = raw(plan.af)
        assert g.startswith("aformat=channel_layouts=stereo")
        assert "pan=5.1|" in g and "join=inputs=6" in g
        # chaque étiquette [x] est produite une fois et consommée une fois
        for label in set(re.findall(r"\[(\w+)\]", g)):
            assert len(re.findall(rf"\[{label}\]", g)) == 2, label
        assert ("crossfeed" in g) == (out == "hp")


def test_surround_mono_est_passe_bas_7khz_et_stereo_ne_l_est_pas():
    assert "lowpass=f=7000" in raw(build_audio(AudioSettings(mode="pl", output="51")).af)
    assert "lowpass=f=7000" not in raw(build_audio(AudioSettings(mode="pl2m", output="51")).af)


def test_retard_nul_omis():
    assert "adelay" not in raw(build_audio(AudioSettings(mode="pl2m", delay_ms=0)).af).split("[d]")[1].split("[e2]")[0]


def test_stereo_elargie():
    plan = build_audio(AudioSettings(mode="wide", surround=100))
    assert plan.channels == "stereo" and "pan=stereo|FL=1*FL|FR=1*FR" in raw(plan.af)


def test_tailles_video():
    assert frame_size("pal", "4:3") == (768, 576)
    assert frame_size("ntsc", "4:3") == (640, 480)
    assert frame_size("pal", "16:9") == (1024, 576)
    assert all(frame_size(s, d)[0] % 2 == 0 for s in ("pal", "secam", "ntsc") for d in ("4:3", "16:9"))


def test_video_off_et_options():
    assert build_video("off") == ""
    g = raw(build_video("ntsc", "4:3", scanlines=True))
    assert "fps=30000/1001" in g and "drawgrid" in g and "saturation=1.08" in g
    assert "drawgrid" not in raw(build_video("pal"))
    assert "fps=25" in raw(build_video("secam"))
    assert set(VIDEO_STANDARDS) == {"off", "pal", "secam", "ntsc"}


# Validation de syntaxe avec un ffmpeg local, UNIQUEMENT pour les tests (jamais livré, jamais appelé par l'application).
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent : validation de syntaxe ignorée")
def test_syntaxe_lavfi_valide():
    def ok(inp, flag, graph):
        r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", inp, flag, graph, "-f", "null", "-"],
                           capture_output=True, text=True)
        assert r.returncode == 0 and not r.stderr.strip(), r.stderr

    for mode in AUDIO_MODES:
        for out in ("hp", "51"):
            plan = build_audio(AudioSettings(mode=mode, output=out, bass_db=3, room=30))
            ok("sine=f=440:d=1,aformat=channel_layouts=stereo", "-af", raw(plan.af))
    for std in ("pal", "secam", "ntsc"):
        for dar in ("4:3", "16:9"):
            ok("testsrc=size=1280x720:rate=30:d=1", "-vf", raw(build_video(std, dar, True)))
