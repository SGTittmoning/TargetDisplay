import os
import subprocess
import sys
import textwrap

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_script(tmp_path, body):
    script = tmp_path / 'skript.py'
    script.write_text(f'import sys\nsys.path.insert(0, {ROOT!r})\n' + textwrap.dedent(body))
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True, check=False)


def test_unbehandelte_exception_endet_mit_exit_code_3(tmp_path):
    result = run_script(tmp_path, '''
        import process_handlers
        process_handlers.install()
        raise RuntimeError("kaputt")
    ''')
    assert result.returncode == 3
    assert 'RuntimeError: kaputt' in result.stderr


def test_sys_exit_bleibt_unberuehrt(tmp_path):
    result = run_script(tmp_path, '''
        import process_handlers
        process_handlers.install()
        sys.exit(2)
    ''')
    assert result.returncode == 2


def test_sigterm_beendet_mit_meldung_und_exit_0(tmp_path):
    result = run_script(tmp_path, '''
        import os, signal, time
        import process_handlers
        process_handlers.install()
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(5)
    ''')
    assert result.returncode == 0
    assert 'SIGTERM empfangen' in result.stderr


def test_import_von_main_hat_keine_nebenwirkungen(tmp_path):
    # Ohne config.yml im Arbeitsverzeichnis; Handler und Ausgaben erst beim
    # Programmstart, nicht beim Import.
    result = run_script(tmp_path, f'''
        import os, signal
        os.chdir({str(tmp_path)!r})
        hook, handler = sys.excepthook, signal.getsignal(signal.SIGTERM)
        import main
        assert sys.excepthook is hook
        assert signal.getsignal(signal.SIGTERM) is handler
    ''')
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''


# Dieselben Handler im eigenen Prozess: nur so erfasst die Abdeckung sie.

def test_handler_sigterm_im_prozess(capsys):
    import process_handlers
    with pytest.raises(SystemExit) as stop:
        process_handlers.handle_sigterm(15, None)
    assert stop.value.code == 0
    assert 'SIGTERM empfangen' in capsys.readouterr().err


def test_handler_unbehandelte_exception_im_prozess(monkeypatch, capsys):
    import process_handlers
    codes = []
    monkeypatch.setattr(process_handlers.os, '_exit', codes.append)
    try:
        raise ValueError('kaputt')
    except ValueError:
        process_handlers.handle_uncaught(*sys.exc_info())
    assert codes == [3]
    assert 'ValueError: kaputt' in capsys.readouterr().err


def test_install_setzt_beide_handler(monkeypatch):
    import signal

    import process_handlers
    monkeypatch.setattr(sys, 'excepthook', sys.excepthook)
    old = signal.getsignal(signal.SIGTERM)
    try:
        process_handlers.install()
        assert signal.getsignal(signal.SIGTERM) is process_handlers.handle_sigterm
        assert sys.excepthook is process_handlers.handle_uncaught
    finally:
        signal.signal(signal.SIGTERM, old)
