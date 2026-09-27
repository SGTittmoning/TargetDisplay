"""Minimaler Ersatz fuer config_with_yaml: laedt eine YAML-Datei und liest
Werte per Punkt-Pfad (z.B. 'video.section_full'). PyYAML kommt ueber das
apt-Paket python3-yaml, genau wie opencv/numpy (siehe requirements.txt) -
keine zusaetzliche Fremdabhaengigkeit fuer eine triviale Aufgabe.
"""
import yaml


class Config:
    def __init__(self, data):
        self._data = data or {}

    def getProperty(self, path):
        node = self._data
        for part in path.split('.'):
            node = node[part]
        return node

    def getPropertyWithDefault(self, path, default):
        try:
            return self.getProperty(path)
        except (KeyError, TypeError):
            # TypeError zusaetzlich zu KeyError: eine Zwischenebene kann in
            # der YAML-Datei auch "null" oder ein Skalar statt eines
            # Mappings sein (z.B. "video: null"), dann wirft node[part]
            # TypeError statt KeyError. Beide Faelle sind fuer den Aufrufer
            # gleichbedeutend mit "Wert nicht vorhanden".
            return default


def load(filename):
    with open(filename, encoding='utf-8') as f:
        return Config(yaml.safe_load(f))
