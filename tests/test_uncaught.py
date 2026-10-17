import os
import subprocess
import sys
import textwrap

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
