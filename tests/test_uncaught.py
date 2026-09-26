import os
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_unbehandelte_exception_endet_mit_exit_code_3(tmp_path):
    # Der excepthook aus main.py wird isoliert aus dem Quelltext geladen: ein
    # Import von main.py braucht config.yml, Display und Kamera.
    source = open(os.path.join(ROOT, 'main.py'), encoding='utf-8').read()
    start = source.index('def _handle_uncaught')
    end = source.index('sys.excepthook = _handle_uncaught')
    script = tmp_path / 'crash.py'
    script.write_text(
        'import os, sys, traceback\n'
        + source[start:end]
        + 'sys.excepthook = _handle_uncaught\n'
        + 'raise RuntimeError("kaputt")\n')
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 3
    assert 'RuntimeError: kaputt' in result.stderr


def test_sys_exit_bleibt_unberuehrt(tmp_path):
    script = tmp_path / 'ok.py'
    script.write_text(textwrap.dedent('''
        import sys
        sys.exit(2)
    '''))
    assert subprocess.run([sys.executable, str(script)]).returncode == 2
