import pytest

import sysinfo


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def test_cpu_auslastung_aus_zwei_messungen(tmp_path):
    path = write(tmp_path, 'stat', 'cpu  100 0 100 700 100 0 0 0 0 0\n')
    cpu = sysinfo.CpuUsage(path)
    assert cpu.percent() is None              # erste Messung: noch kein Abstand
    # +400 gesamt, davon +100 untaetig (idle + iowait): 75 % belegt.
    write(tmp_path, 'stat', 'cpu  250 0 250 750 150 0 0 0 0 0\n')
    assert cpu.percent() == pytest.approx(75.0)


def test_cpu_ohne_datei(tmp_path):
    assert sysinfo.CpuUsage(str(tmp_path / 'fehlt')).percent() is None


def test_ram_belegt(tmp_path):
    path = write(tmp_path, 'meminfo', 'MemTotal:  4000 kB\nMemFree:  1000 kB\nMemAvailable:  3000 kB\n')
    assert sysinfo.ram_percent(path) == pytest.approx(25.0)


def test_ram_unlesbar(tmp_path):
    assert sysinfo.ram_percent(write(tmp_path, 'meminfo', 'MemTotal: 4000 kB\n')) is None


def test_temperatur(tmp_path):
    assert sysinfo.cpu_temperature(write(tmp_path, 'temp', '79854\n')) == pytest.approx(79.854)
    assert sysinfo.cpu_temperature(str(tmp_path / 'fehlt')) is None


@pytest.mark.parametrize('temp, level', [(None, 'ok'), (69.9, 'ok'), (70, 'warn'), (79.9, 'warn'), (80, 'hot')])
def test_temperaturstufe(temp, level):
    assert sysinfo.temperature_level(temp) == level


def test_texte():
    assert sysinfo.usage_text(38.4, 9.2) == 'CPU 38 %   RAM 9 %'
    assert sysinfo.usage_text(None, 9.2) == 'CPU - %   RAM 9 %'
    assert sysinfo.temperature_text(64.8) == '65 °C'
    assert sysinfo.temperature_text(None) == ''
    assert sysinfo.load_text((1.03, 0.69, 0.29)) == 'Last 1,03 0,69 0,29'
    assert sysinfo.load_text(None) == ''
