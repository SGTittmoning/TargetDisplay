import numpy as np

from overlays import blend_logo_centered


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
