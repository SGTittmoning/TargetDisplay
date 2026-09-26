import pytest

from logutil import mask_credentials

URL = 'rtmp://cam:geheim@192.168.11.11:1935/bcs/channel0_main.bcs?channel=0&stream=0&user=admin&password=Pa55!w0rd'


def test_userinfo_und_query_werden_maskiert():
    masked = mask_credentials(URL)
    assert 'geheim' not in masked
    assert 'Pa55!w0rd' not in masked
    assert 'admin' not in masked
    assert masked == 'rtmp://***@192.168.11.11:1935/bcs/channel0_main.bcs?channel=0&stream=0&user=***&password=***'


def test_exception_text_mit_url():
    msg = f"[Errno 111] Connection refused: '{URL}'"
    masked = mask_credentials(msg)
    assert 'geheim' not in masked and 'Pa55!w0rd' not in masked
    assert masked.endswith("password=***'")


@pytest.mark.parametrize("text", [
    'rtmp://192.168.3.41:1935/live/test',
    'Input/output error',
    'rtsp://kamera/stream?channel=0&subtype=1',
])
def test_text_ohne_zugangsdaten_bleibt_unveraendert(text):
    assert mask_credentials(text) == text


def test_gross_und_kleinschreibung_der_parameter():
    assert mask_credentials('x://h/p?PASSWORD=abc&Token=def') == 'x://h/p?PASSWORD=***&Token=***'


def test_kein_string_wird_zu_string():
    assert mask_credentials(ValueError('boom')) == 'boom'
