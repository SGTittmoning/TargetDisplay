"""Hilfsfunktionen fuer Log-Ausgaben."""
import re

# rtmp://benutzer:passwort@host/... -> rtmp://***@host/...
_USERINFO = re.compile(r'(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/@\s\'"]+@')
# ...?user=admin&password=geheim -> ...?user=***&password=***
_QUERY_SECRET = re.compile(
    r'(?P<key>[?&;](?:user|username|usr|password|passwd|pass|pwd|token|key|secret)=)[^&\s\'"]*',
    re.IGNORECASE)


def mask_credentials(text):
    """Ersetzt Zugangsdaten in URLs (Userinfo und Query-Parameter) durch ***."""
    text = _USERINFO.sub(r'\g<scheme>***@', str(text))
    return _QUERY_SECRET.sub(r'\g<key>***', text)
