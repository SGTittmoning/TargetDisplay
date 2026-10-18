"""Erzeugt aus den Screenshots die Fassungen mit Nummernmarken fuer das
Handbuch (screenshots/NN_*_nummern.png). Die Nummern entsprechen den Legenden;
die Farbe zeigt die Gruppe. Aufruf: python3 nummern.py (Pillow noetig)."""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, 'screenshots')
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
RADIUS = 13

INK, ZOOM, BLINK, TIMER, GREY, ADMIN = '#201c18', '#2f6690', '#a85a15', '#a3311f', '#6b6355', '#3c6e4a'

# Mittelpunkt der Marke, Nummer, Farbe; je Bild.
MARKS = {
    '01_hauptbildschirm': [
        (160, 32, 1, INK), (356, 16, 2, INK), (416, 16, 3, INK),
        (22, 120, 1, ZOOM), (175, 120, 2, ZOOM), (328, 120, 3, ZOOM), (520, 50, 4, ZOOM), (1112, 738, 5, ZOOM),
        (22, 274, 1, BLINK), (175, 274, 2, BLINK), (328, 274, 3, BLINK),
        (22, 406, 1, TIMER), (175, 406, 2, TIMER), (328, 406, 3, TIMER),
        (22, 498, 4, TIMER), (328, 498, 5, TIMER),
        (22, 637, 1, GREY), (100, 778, 2, GREY),
    ],
    '08_pin_eingabe': [
        (508, 282, 1, ADMIN), (508, 540, 2, ADMIN), (702, 540, 3, ADMIN), (534, 630, 4, ADMIN),
    ],
    '09_einstellungen_menue': [
        (543, 235 + 73 * i, i + 1, ADMIN) for i in range(5)
    ] + [(543, 600, 6, ADMIN)],
}


def main():
    font = ImageFont.truetype(FONT, 16)
    for name, marks in MARKS.items():
        img = Image.open(os.path.join(SHOTS, name + '.png')).convert('RGB')
        draw = ImageDraw.Draw(img)
        for x, y, number, color in marks:
            draw.ellipse((x - RADIUS, y - RADIUS, x + RADIUS, y + RADIUS), fill=color, outline='white', width=2)
            draw.text((x, y), str(number), fill='white', font=font, anchor='mm')
        img.save(os.path.join(SHOTS, name + '_nummern.png'))


if __name__ == '__main__':
    main()
