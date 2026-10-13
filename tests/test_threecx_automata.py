'''Tests for threecx_automata.ThreeCX'''
# pytest fixtures are passed in as same-named arguments, and _build_url is
# worth testing directly.
# pylint: disable=redefined-outer-name,protected-access
import re

import pytest

import threecx_automata

DOMAIN = 'pbx.example.com'
BASE = f'https://{DOMAIN}'
TOKENS = {
    'access_token': 'access',
    'refresh_token': 'refresh',
    'auth_token': 'auth',
}


@pytest.fixture
def gui():
    '''ThreeCX preloaded with tokens, so no login is needed.'''
    return threecx_automata.ThreeCX(DOMAIN, tokens=TOKENS)


def test_version():
    '''__version__ is a plain X.Y.Z string.'''
    assert re.fullmatch(r'\d+\.\d+\.\d+', threecx_automata.__version__)


@pytest.mark.parametrize(('domain', 'ssl', 'path', 'expected'), [
    (DOMAIN, True, '/xapi/v1/Backups', f'{BASE}/xapi/v1/Backups'),
    (DOMAIN, True, 'xapi/v1/Backups', f'{BASE}/xapi/v1/Backups'),
    (f' {DOMAIN}/ ', True, '/x', f'{BASE}/x'),
    (DOMAIN, False, '/x', f'http://{DOMAIN}/x'),
])
def test_build_url(domain, ssl, path, expected):
    '''Scheme, surrounding whitespace and slashes are normalised.'''
    assert threecx_automata.ThreeCX(domain, ssl=ssl)._build_url(path) == expected


def test_tokens_round_trip(gui):
    '''Dumped tokens can be fed back into a new instance.'''
    assert gui.dump_tokens() == TOKENS
    assert threecx_automata.ThreeCX(DOMAIN, tokens=gui.dump_tokens()).dump_tokens() == TOKENS


def test_no_tokens():
    '''Without tokens every token is None.'''
    assert threecx_automata.ThreeCX(DOMAIN).dump_tokens() == dict.fromkeys(TOKENS)


def test_login(requests_mock):
    '''A successful login stores all three tokens.'''
    requests_mock.post(f'{BASE}/webclient/api/Login/GetAccessToken', json={
        'Status': 'AuthSuccess',
        'Token': {
            'token_type': 'Bearer',
            'access_token': 'access',
            'refresh_token': 'refresh',
        },
    })
    requests_mock.post(f'{BASE}/connect/token', json={'access_token': 'auth'})

    gui = threecx_automata.ThreeCX(DOMAIN)
    assert gui.login('user', 'pass') is True
    assert gui.dump_tokens() == TOKENS
    assert requests_mock.request_history[0].json()['Username'] == 'user'


def test_login_http_error(requests_mock):
    '''A non-200 login response raises HttpError.'''
    requests_mock.post(f'{BASE}/webclient/api/Login/GetAccessToken', status_code=401)
    with pytest.raises(threecx_automata.HttpError):
        threecx_automata.ThreeCX(DOMAIN).login('user', 'pass')


def test_login_bad_status(requests_mock):
    '''A non-AuthSuccess status raises InterfaceError.'''
    requests_mock.post(f'{BASE}/webclient/api/Login/GetAccessToken',
                       json={'Status': 'AuthFailed'})
    with pytest.raises(threecx_automata.InterfaceError):
        threecx_automata.ThreeCX(DOMAIN).login('user', 'pass')


def test_backup_fetch_list(gui, requests_mock):
    '''Lists backups, optionally filtered by filename, with auth.'''
    backups = [{'FileName': 'a.zip'}, {'FileName': 'b.zip'}]
    requests_mock.get(f'{BASE}/xapi/v1/Backups', json={'value': backups})

    assert gui.backup_fetch_list() == backups
    assert gui.backup_fetch_list('b.zip') == [{'FileName': 'b.zip'}]
    assert requests_mock.last_request.headers['Authorization'] == 'Bearer auth'


def test_backup_start(gui, requests_mock):
    '''Uses the given filename or a dated default.'''
    requests_mock.post(f'{BASE}/xapi/v1/Backups/Pbx.Backup', status_code=204)
    assert gui.backup_start('test.zip') == 'test.zip'
    assert re.fullmatch(r'CDRDump-\d{4}-\d{2}-\d{2}\.zip', gui.backup_start())


def test_backup_start_duplicate(gui, requests_mock):
    '''A duplicate backup is treated as success.'''
    requests_mock.post(f'{BASE}/xapi/v1/Backups/Pbx.Backup', status_code=400, json={
        'error': {'details': [{'message': 'WARNINGS.XAPI.DUPLICATE'}]},
    })
    assert gui.backup_start('test.zip') == 'test.zip'


def test_backup_delete(gui, requests_mock):
    '''204 succeeds, anything else raises HttpError.'''
    requests_mock.delete(f"{BASE}/xapi/v1/Backups('test.zip')", status_code=204)
    gui.backup_delete('test.zip')

    requests_mock.delete(f"{BASE}/xapi/v1/Backups('test.zip')", status_code=404)
    with pytest.raises(threecx_automata.HttpError):
        gui.backup_delete('test.zip')


def test_fetch_status(gui, requests_mock):
    '''Returns raw text by default, parsed JSON on request.'''
    requests_mock.get(f'{BASE}/xapi/v1/SystemStatus', json={'Version': '20.0'})
    assert gui.fetch_status() == '{"Version": "20.0"}'
    assert gui.fetch_status(raw_json=False) == {'Version': '20.0'}
