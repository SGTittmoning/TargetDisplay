from watchdog import stale_check_due


def test_keine_pruefung_waehrend_timer():
    assert not stale_check_due(False, True, now=1000, resumed_at=0, grace=10)


def test_keine_pruefung_bei_video_aus():
    assert not stale_check_due(False, False, now=1000, resumed_at=0, grace=10)


def test_pruefung_bei_aktivem_video():
    assert stale_check_due(True, False, now=1000, resumed_at=0, grace=10)


def test_schonfrist_nach_wiedereinschalten():
    assert not stale_check_due(True, False, now=1005, resumed_at=1000, grace=10)
    assert stale_check_due(True, False, now=1011, resumed_at=1000, grace=10)
