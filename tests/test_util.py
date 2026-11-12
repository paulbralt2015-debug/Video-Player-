import os
import tempfile

from twinsplayer.util import expand_media, fmt_time, natural_key, parse_time


def test_fmt_time():
    assert fmt_time(0) == "0:00" and fmt_time(75) == "1:15" and fmt_time(3725) == "1:02:05"
    assert fmt_time(None) == "0:00" and fmt_time(float("inf")) == "0:00" and fmt_time(float("nan")) == "0:00"


def test_parse_time():
    assert parse_time("1:25:30") == 5130 and parse_time("85:03") == 5103 and parse_time("90") == 90
    assert parse_time("12,5") == 12.5
    for bad in ("", "a:b", "1:2:3:4", "-5", "inf"):
        assert parse_time(bad) is None


def test_tri_naturel_et_exploration():
    assert sorted(["ep10", "ep2", "ep1"], key=natural_key) == ["ep1", "ep2", "ep10"]
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "sub"))
    for n in ("b10.mp4", "b2.mkv", "notes.txt", os.path.join("sub", "c.MP3")):
        open(os.path.join(d, n), "w").close()
    got = [os.path.relpath(p, d) for p in expand_media([d])]
    assert got == ["b2.mkv", "b10.mp4", os.path.join("sub", "c.MP3")]
    assert expand_media([os.path.join(d, "notes.txt")]) == []
