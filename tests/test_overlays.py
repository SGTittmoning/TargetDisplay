import numpy as np
import pytest

import overlays as ov
from overlays import blend_logo_centered


@pytest.fixture
def texts(monkeypatch):
    """Alle Texte, die in ein Bild geschrieben werden, in Reihenfolge."""
    drawn = []
    real = ov.cv2.putText
    monkeypatch.setattr(ov.cv2, 'putText', lambda img, text, *a, **k: (drawn.append(text), real(img, text, *a, **k))[1])
    return drawn


def make_canvas():
    return np.zeros((200, 200, 3), np.uint8)


def test_rgba_logo_wird_alpha_ueberblendet():
    logo = np.zeros((50, 50, 4), np.uint8)
    logo[..., :3] = 255  # weiss
    logo[..., 3] = 255  # voll deckend
    canvas = blend_logo_centered(make_canvas(), logo)
    assert canvas[100, 100].tolist() == [255, 255, 255]


def test_bgr_logo_ohne_alpha():
    logo = np.full((50, 50, 3), 200, np.uint8)
    canvas = blend_logo_centered(make_canvas(), logo)
    assert canvas[100, 100].tolist() == [200, 200, 200]


def test_graustufen_logo_ohne_kanaldimension_stuerzt_nicht_ab():
    # cv2.imread(..., IMREAD_UNCHANGED) liefert fuer ein reines Graustufenbild
    # ein 2D-Array (H, W) ohne dritte Dimension.
    logo = np.full((50, 50), 128, np.uint8)
    canvas = blend_logo_centered(make_canvas(), logo)
    assert canvas[100, 100].tolist() == [128, 128, 128]


def test_graustufen_logo_mit_einzelnem_kanal():
    logo = np.full((50, 50, 1), 128, np.uint8)
    canvas = blend_logo_centered(make_canvas(), logo)
    assert canvas[100, 100].tolist() == [128, 128, 128]


def test_replay_markierung_rahmen_und_zaehler():
    from overlays import REPLAY_COLOR, draw_replay_marker
    frame = np.zeros((300, 300, 3), np.uint8)
    draw_replay_marker(frame, 'Durchgang 2/5  1,4 s')
    assert frame[150, 2].tolist() == list(REPLAY_COLOR)
    assert frame[150, 297].tolist() == list(REPLAY_COLOR)
    assert frame[150, 150].tolist() == [0, 0, 0]


def test_replay_trennbild_ist_rot():
    from overlays import replay_separator
    frame = replay_separator((200, 100), 'Durchgang 2')
    assert frame.shape == (100, 200, 3)
    assert frame[5, 5].tolist() == [0, 0, 255]


def test_replay_schlussbild_ganz_rot():
    from overlays import replay_separator
    frame = replay_separator((200, 100), '')
    assert (frame == [0, 0, 255]).all()


def test_timer_bild_mit_durchgang_und_countdown():
    import timerlib
    green = ov.timer_frame((600, 400), timerlib.TimerState('green', 2, 3, False))
    assert green.shape == (400, 600, 3)
    assert tuple(green[390, 300]) == (0, 255, 0)
    assert (green[30:140, 15:140] == 0).all(axis=2).any()   # Durchgangszahl
    red = ov.timer_frame((600, 400), timerlib.TimerState('red', None, None, False))
    assert (red == (0, 0, 255)).all()


def test_video_aus_bild_ohne_und_mit_logo():
    assert not ov.blank_frame((300, 200), None).any()
    logo = np.full((50, 50, 4), 255, np.uint8)
    with_logo = ov.blank_frame((300, 200), logo)
    assert with_logo[100, 150].max() == 255 and not with_logo[0, 0].any()


def test_texte_im_timer_bild(texts):
    import timerlib
    ov.timer_frame((600, 400), timerlib.TimerState('green', 2, 3, False))
    assert texts == ['2', '3']
    texts.clear()
    ov.timer_frame((600, 400), timerlib.TimerState('red', None, 7, False))
    assert texts == ['7']


def test_texte_im_replay(texts):
    ov.draw_replay_marker(np.zeros((300, 300, 3), np.uint8), 'Durchgang 2/5  1,4 s')
    assert texts == ['REPLAY', 'Durchgang 2/5  1,4 s']
    texts.clear()
    ov.replay_separator((300, 200), 'Durchgang 3/5')
    ov.replay_separator((300, 200), '')
    ov.replay_no_frame((300, 200))
    assert texts == ['Durchgang 3/5', 'kein Bild']


def test_logo_vorrang_fuer_die_eigene_datei(tmp_path, monkeypatch):
    import cv2

    import main
    (tmp_path / 'ressources').mkdir()
    cv2.imwrite(str(tmp_path / 'ressources' / 'logo.png'), np.full((8, 8, 3), 10, np.uint8))
    monkeypatch.chdir(tmp_path)
    assert main.load_logo()[0, 0, 0] == 10
    cv2.imwrite(str(tmp_path / 'ressources' / 'logo-custom.png'), np.full((8, 8, 3), 200, np.uint8))
    assert main.load_logo()[0, 0, 0] == 200
    (tmp_path / 'ressources' / 'logo-custom.png').unlink()
    (tmp_path / 'ressources' / 'logo.png').unlink()
    assert main.load_logo() is None
