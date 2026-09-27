import cv2
import numpy as np


def blend_logo_centered(canvas, logo_rgba, margin_ratio=0.05):
    # skaliert ein BGRA-Logo unter Beibehaltung des Seitenverhaeltnisses so
    # gross wie moeglich (minus Rand) und zeichnet es alpha-transparent
    # zentriert auf den Canvas
    canvas_h, canvas_w = canvas.shape[:2]
    margin = int(min(canvas_w, canvas_h) * margin_ratio)
    max_w = canvas_w - 2 * margin
    max_h = canvas_h - 2 * margin
    logo_h, logo_w = logo_rgba.shape[:2]
    scale = min(max_w / logo_w, max_h / logo_h)
    new_w, new_h = int(logo_w * scale), int(logo_h * scale)
    logo_rgba = cv2.resize(logo_rgba, (new_w, new_h))
    x0 = (canvas_w - new_w) // 2
    y0 = (canvas_h - new_h) // 2
    if logo_rgba.shape[2] == 4:
        alpha = logo_rgba[:, :, 3:4].astype(np.float32) / 255.0
        logo_bgr = logo_rgba[:, :, :3].astype(np.float32)
        roi = canvas[y0:y0+new_h, x0:x0+new_w].astype(np.float32)
        canvas[y0:y0+new_h, x0:x0+new_w] = (alpha * logo_bgr + (1 - alpha) * roi).astype(np.uint8)
    else:
        canvas[y0:y0+new_h, x0:x0+new_w] = logo_rgba[:, :, :3]
    return canvas


def draw_timer_countdown(frame, seconds_left):
    # Zentriert und gross dargestellt. getTextSize() liefert die
    # tatsaechliche Breite/Hoehe des gerenderten Textes, dadurch klappt die
    # Zentrierung unabhaengig von Ziffernanzahl (1 vs. 2-stellig).
    text = str(seconds_left)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 6.5
    thickness = 11
    (tw, th), _ = cv2.getTextSize(text, font, font_scale, thickness)
    x = (frame.shape[1] - tw) // 2
    y = (frame.shape[0] + th) // 2
    cv2.putText(frame, text, (x, y), font, font_scale, (0, 0, 0), thickness, cv2.LINE_AA)
