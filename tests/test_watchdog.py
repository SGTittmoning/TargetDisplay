import pytest

from watchdog import stale_check_due


@pytest.mark.parametrize('mode', ['timer', 'blank', 'replay'])
def test_keine_pruefung_ohne_livebild(mode):
    assert not stale_check_due(mode, now=1000, resumed_at=0, grace=10)


def test_pruefung_im_livebild():
    assert stale_check_due('live', now=1000, resumed_at=0, grace=10)


def test_schonfrist_nach_rueckkehr_zum_livebild():
    assert not stale_check_due('live', now=1005, resumed_at=1000, grace=10)
    assert stale_check_due('live', now=1011, resumed_at=1000, grace=10)
