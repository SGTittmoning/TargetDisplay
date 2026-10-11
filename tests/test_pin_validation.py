import pytest

import flows
from settings_store import DEFAULT_PIN


def test_gueltiger_pin_wird_uebernommen():
    assert flows.validate_new_pin('4711') == (True, '4711')
    assert flows.validate_new_pin('123456') == (True, '123456')


@pytest.mark.parametrize('entered', ['', '1', '123', '1234567'])
def test_laenge_ausserhalb_4_bis_6_wird_abgelehnt(entered):
    ok, (text, wait) = flows.validate_new_pin(entered)
    assert not ok
    assert text == '4-6 Ziffern'
    assert wait < 2


def test_standard_pin_wird_abgelehnt_mit_hinweis():
    ok, (text, wait) = flows.validate_new_pin(DEFAULT_PIN)
    assert not ok
    assert text == 'nicht erlaubt'
    assert wait > 0


def test_aehnliche_pins_sind_erlaubt():
    assert flows.validate_new_pin('12345')[0]
    assert flows.validate_new_pin('0123')[0]
    assert flows.validate_new_pin('4321')[0]
