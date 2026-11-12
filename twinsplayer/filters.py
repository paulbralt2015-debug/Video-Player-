"""Constructeurs de chaînes de filtres pour libmpv (syntaxe lavfi).

Aucune dépendance à Qt ou à mpv : ce module est testable seul.
Les chaînes sont passées à mpv sous la forme ``lavfi=graph=%N%<graphe>``
(citation par longueur d'octets, qui évite tout problème d'échappement).
"""
from __future__ import annotations

from dataclasses import dataclass

# ----------------------------------------------------------------- audio ---

AUDIO_MODES = {
    "stereo": "Stéréo (sans traitement)",
    "wide": "Stéréo élargie",
    "dss": "Dolby Surround (matriciel)",
    "pl": "Dolby Pro Logic (4 canaux)",
    "pl2m": "Pro Logic II Movie",
    "pl2x": "Pro Logic II Music",
    "pl2mx": "Pro Logic II Matrix",
}

# c : part de centre soustraite aux voies avant ; mono : surround mono filtré ;
# lp : coupure passe-bas du surround (Hz, 0 = aucune) ; sm : gain du surround.
_PROFILES = {
    "dss": dict(c=0.0, mono=True, lp=7000, sm=1.0),
    "pl": dict(c=1.0, mono=True, lp=7000, sm=1.0),
    "pl2m": dict(c=1.0, mono=False, lp=0, sm=1.0),
    "pl2x": dict(c=0.7, mono=False, lp=0, sm=0.75),
    "pl2mx": dict(c=1.0, mono=False, lp=0, sm=0.9),
}


@dataclass
class AudioSettings:
    mode: str = "stereo"
    output: str = "hp"       # "hp" = casque, "51" = enceintes 5.1
    surround: int = 100      # 0..300 (%)
    center: int = 100        # 0..200 (%)
    delay_ms: int = 15       # 0..60
    bass_db: int = 0         # -6..12
    room: int = 0            # 0..100


@dataclass
class AudioPlan:
    af: str          # valeur de la propriété mpv « af » ('' = aucun filtre)
    channels: str    # valeur de « audio-channels »


def _quote(graph: str) -> str:
    """Citation mpv « %N% » : N = nombre d'octets de la chaîne."""
    return f"%{len(graph.encode('utf-8'))}%{graph}"


def _wrap(graph: str) -> str:
    return f"lavfi=graph={_quote(graph)}" if graph else ""


def _num(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _expr(terms) -> str:
    """[(1, 'FL'), (-0.5, 'FR')] -> '1*FL-0.5*FR' (termes nuls ignorés)."""
    out = ""
    for gain, ch in terms:
        if abs(gain) < 1e-6:
            continue
        sign = "-" if gain < 0 else ("+" if out else "")
        out += f"{sign}{_num(abs(gain))}*{ch}"
    return out or "0*FL"


def _echo(room: int) -> str:
    r = room / 100.0
    return ("aecho=in_gain=0.9:out_gain=0.9:delays=43|71|113:"
            f"decays={_num(0.5 * r)}|{_num(0.35 * r)}|{_num(0.25 * r)}")


def build_audio(s: AudioSettings) -> AudioPlan:
    pre = []
    if s.bass_db:
        pre.append(f"bass=g={int(s.bass_db)}:f=120")
    if s.room > 0:
        pre.append(_echo(s.room))

    if s.mode == "stereo":
        return AudioPlan(_wrap(",".join(pre)), "auto-safe")

    # Les modes matriciels attendent une bande son stéréo Lt/Rt : toute autre
    # source est d'abord ramenée en stéréo.
    chain = ["aformat=channel_layouts=stereo"] + pre
    sl = s.surround / 100.0
    cl = s.center / 100.0

    if s.mode == "wide":
        a, b = 0.5 + 0.5 * sl, 0.5 - 0.5 * sl
        chain.append(f"pan=stereo|FL={_expr([(a, 'FL'), (b, 'FR')])}"
                     f"|FR={_expr([(b, 'FL'), (a, 'FR')])}")
        return AudioPlan(_wrap(",".join(chain)), "stereo")

    p = _PROFILES[s.mode]
    pc, k = p["c"], 0.707
    d1 = max(0, int(s.delay_ms))
    fl = _expr([(1 - k * k * pc, "FL"), (-k * k * pc, "FR")])
    fr = _expr([(-k * k * pc, "FL"), (1 - k * k * pc, "FR")])
    fc = _expr([(k * cl * pc, "FL"), (k * cl * pc, "FR")])
    lfe = _expr([(0.35, "FL"), (0.35, "FR")])
    g = sl * p["sm"]
    if p["mono"]:
        bl = br = _expr([(k * g, "FL"), (-k * g, "FR")])
        dl = dr = d1
    else:
        bl = _expr([(g, "FL"), (-0.6 * g, "FR")])
        br = _expr([(0.6 * g, "FL"), (-g, "FR")])
        dl, dr = d1, d1 + 3
    chain.append(f"pan=5.1|FL={fl}|FR={fr}|FC={fc}|LFE={lfe}|BL={bl}|BR={br}")
    chain.append("channelsplit=channel_layout=5.1[a][b][c][d][e][f]")

    def surround_path(label: str, out: str, delay: int) -> str:
        f = []
        if p["lp"]:
            f.append(f"lowpass=f={p['lp']}")
        if delay > 0:
            f.append(f"adelay={delay}")
        return f"[{label}]{','.join(f) if f else 'anull'}[{out}]"

    graph = (",".join(chain) + ";[d]lowpass=f=120[d2];"
             + surround_path("e", "e2", dl) + ";" + surround_path("f", "f2", dr)
             + ";[a][b][c][d2][e2][f2]join=inputs=6:channel_layout=5.1:"
             "map=0.0-FL|1.0-FR|2.0-FC|3.0-LFE|4.0-BL|5.0-BR")

    if s.output == "51":
        return AudioPlan(_wrap(graph), "5.1")
    # Casque : repli stéréo normalisé + crossfeed (pas de HRTF sans fichier SOFA).
    graph += (",pan=stereo|FL<FL+0.707*FC+0.6*BL|FR<FR+0.707*FC+0.6*BR"
              ",crossfeed=strength=0.35")
    return AudioPlan(_wrap(graph), "stereo")


# ----------------------------------------------------------------- vidéo ---

VIDEO_STANDARDS = {
    "off": "Original",
    "pal": "PAL · 25 i/s · 576 lignes",
    "secam": "SECAM · 25 i/s · 576 lignes",
    "ntsc": "NTSC · 29,97 i/s · 480 lignes",
}

# sigma / sigma_v : flou de chrominance horizontal / vertical (pixels des plans U/V)
_STD = {
    "pal": dict(fps="25", lines=576, sigma=2.0, sigma_v=0.9, sat=1.0, luma=0.0, contrast=1.0, bright=0.0),
    "secam": dict(fps="25", lines=576, sigma=2.0, sigma_v=0.0, sat=0.92, luma=0.0, contrast=1.0, bright=0.0),
    "ntsc": dict(fps="30000/1001", lines=480, sigma=3.0, sigma_v=0.0, sat=1.08, luma=0.35, contrast=0.925, bright=0.037),
}


def frame_size(std: str, dar: str) -> tuple[int, int]:
    h = _STD[std]["lines"]
    w = round(h * (16 / 9 if dar == "16:9" else 4 / 3))
    return w - (w % 2), h


def build_video(std: str, dar: str = "4:3", scanlines: bool = False) -> str:
    if std not in _STD:
        return ""
    s = _STD[std]
    w, h = frame_size(std, dar)
    f = [
        f"fps={s['fps']}",
        f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=bicubic",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
        "setsar=1",
        "format=yuv420p",
        f"gblur=sigma={_num(s['sigma'])}:sigmaV={_num(s['sigma_v'])}:planes=6",
    ]
    if s["luma"]:
        f.append(f"gblur=sigma={_num(s['luma'])}:sigmaV=0:planes=1")
    if s["sat"] != 1.0 or s["contrast"] != 1.0 or s["bright"]:
        f.append(f"eq=contrast={_num(s['contrast'])}:brightness={_num(s['bright'])}"
                 f":saturation={_num(s['sat'])}")
    if scanlines:
        f.append("drawgrid=x=-1:y=0:w=2*iw:h=2:t=1:c=black@0.14")
    return _wrap(",".join(f))
