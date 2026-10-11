# boot-overrides — Beispiel

Aufbau der Vorbelegung, die `ansible/boot-overrides.yml` auf ein Geraet
einspielt. **Die Werte hier sind Platzhalter** und passen zu keiner echten
Kamera-Ausrichtung — ein Geraet damit zu betreiben waere falsch kalibriert.

```
ansible/boot-overrides/          # lokal anlegen, ist gitignored
└── stand1/                      # ein Verzeichnis je Geraet/Stand
    ├── targetdisplay-active-stand.json
    └── targetdisplay-sections.json
```

| Datei | Inhalt |
|---|---|
| `targetdisplay-active-stand.json` | `{"id": "<stand-id>"}` — muss eine `id` aus `targetdisplay_stands` im Inventory sein |
| `targetdisplay-sections.json` | `section_full` und `section_detail`: je vier Eckpunkte `[x, y]` in Pixeln des nativen Kamerabilds (z.B. 2560×1920). Die Reihenfolge ist beliebig, die App sortiert selbst; der Editor liefert links oben, rechts oben, links unten, rechts unten |

## Echte Werte gewinnen

Auf einem bereits kalibrierten Geraet die Datei auslesen:

```bash
ssh <host> cat /boot/firmware/targetdisplay-sections.json
```

und unter `ansible/boot-overrides/<stand>/` ablegen. Nuetzlich, wenn ein
Geraet (z.B. ein Tauschgeraet) exakt dieselbe Position und Kameraausrichtung
wie zuvor bekommt. Bei jeder Abweichung lieber den Assistenten regulaer
durchlaufen lassen, statt eine falsche Kalibrierung vorzubelegen.

## Einspielen

```bash
ansible-playbook -i inventory.yml boot-overrides.yml --limit <host> -e stand=stand1
```

Siehe Kopfkommentar in `boot-overrides.yml`.
