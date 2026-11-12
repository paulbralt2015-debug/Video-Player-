import tempfile
from pathlib import Path

from twinsplayer.store import MAX_POSITIONS, Store, file_key


def fresh():
    d = tempfile.mkdtemp()
    return Store(Path(d) / "s.json"), Path(d) / "s.json"


def test_position_et_persistance():
    s, path = fresh()
    k = file_key("film.mkv", 123)
    s.set_pos(k, 61.234)
    s.flush()
    assert Store(path).get_pos(k) == 61.2
    assert Store(path).get_pos(file_key("film.mkv", 124)) is None   # même nom, autre taille


def test_plafond_des_positions():
    s, _ = fresh()
    for i in range(MAX_POSITIONS + 50):
        s.set_pos(f"f{i}", i)
    assert len(s.data["pos"]) == MAX_POSITIONS
    assert s.get_pos("f0") is None and s.get_pos(f"f{MAX_POSITIONS + 49}") is not None


def test_marque_pages_tries_sans_doublon():
    s, _ = fresh()
    for t in (30.9, 5, 30.2, 12):
        s.add_bookmark("k", t)
    assert s.bookmarks("k") == [5, 12, 30]
    s.remove_bookmark("k", 1)
    assert s.bookmarks("k") == [5, 30]
    s.remove_bookmark("k", 99)       # hors limites : sans effet
    assert s.bookmarks("k") == [5, 30]


def test_mots_bloques_et_prefs():
    s, path = fresh()
    s.set_blocked(["  Foo ", "", "BAR"])
    s.set_pref("dark", False)
    s.flush()
    t = Store(path)
    assert t.blocked() == ["foo", "bar"] and t.pref("dark") is False
    assert t.is_blocked("MonFOOfilm.mp4") and not t.is_blocked("autre.mp4")


def test_fichier_corrompu_ignore():
    d = tempfile.mkdtemp()
    p = Path(d) / "s.json"
    p.write_text("{pas du json", encoding="utf-8")
    assert Store(p).blocked() == []
