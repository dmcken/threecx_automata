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


def test_odata_params():
    '''Options get a '$' prefix, None drops a default, bools are lowercased.'''
    params = threecx_automata.ThreeCX._odata_params(
        {'orderby': 'Number', 'expand': 'Groups'},
        {'expand': None, 'top': 5, 'count': True},
    )
    assert params == {'$orderby': 'Number', '$top': 5, '$count': 'true'}


def test_odata_params_unknown():
    '''Unknown options are rejected instead of silently ignored.'''
    with pytest.raises(TypeError):
        threecx_automata.ThreeCX._odata_params(None, {'sellect': 'Id'})


@pytest.mark.parametrize(('body', 'expected'), [
    ({'@odata.context': 'x', 'value': [1, 2]}, [1, 2]),
    ({'@odata.context': 'x', 'value': 7}, 7),
    ({'@odata.context': 'x', 'Name': 'a'}, {'Name': 'a'}),
])
def test_unwrap(body, expected):
    '''Collections and scalars lose the value wrapper, entities the metadata.'''
    assert threecx_automata.ThreeCX._unwrap(body) == expected


@pytest.mark.parametrize(('method', 'path'), [
    ('fetch_system_parameters', '/xapi/v1/Defs/Pbx.GetSystemParameters()'),
    ('fetch_license_status', '/xapi/v1/LicenseStatus'),
    ('fetch_trunks', '/xapi/v1/Trunks'),
    ('fetch_voicemail_settings', '/xapi/v1/VoicemailSettings'),
    ('fetch_network_interfaces', '/xapi/v1/NetworkSettings/Pbx.GetIfaces()'),
])
def test_fetch_paths(gui, requests_mock, method, path):
    '''Fetch methods hit the matching xapi resource and unwrap it.'''
    requests_mock.get(f'{BASE}{path}', json={'@odata.context': 'x', 'value': ['ok']})
    assert getattr(gui, method)() == ['ok']


def test_fetch_odata_passthrough(gui, requests_mock):
    '''OData keyword arguments become query parameters.'''
    requests_mock.get(f'{BASE}/xapi/v1/EventLogs', json={'value': []})
    gui.fetch_event_logs(top=10, filter="Type eq 'Error'")
    assert requests_mock.last_request.qs == {
        '$orderby': ['timegenerated desc'],
        '$top': ['10'],
        '$filter': ["type eq 'error'"],
    }


@pytest.mark.parametrize(('kwargs', 'expected'), [
    ({}, "not startswith(name,'___favorites___')"),
    ({'filter': "Number eq '1'"},
     "(number eq '1') and not startswith(name,'___favorites___')"),
    ({'include_favorites': True}, None),
])
def test_fetch_groups_favorites(gui, requests_mock, kwargs, expected):
    '''Favourites groups are filtered out unless asked for.'''
    requests_mock.get(f'{BASE}/xapi/v1/Groups', json={'value': []})
    gui.fetch_groups(**kwargs)
    assert requests_mock.last_request.qs.get('$filter', [None])[0] == expected


def test_fetch_phone_registrars_skips_empty(gui, requests_mock):
    '''Empty MACs would make the server reject the whole request.'''
    requests_mock.post(f'{BASE}/xapi/v1/Users/Pbx.GetPhoneRegistrars', json={'value': []})
    gui.fetch_phone_registrars(['AA', '', 'BB'])
    assert requests_mock.last_request.json() == {'macs': ['AA', 'BB']}


def test_reset_user_password(gui, requests_mock):
    """Only the web client password, emailed: SIP credentials stay, so phones keep working."""
    requests_mock.post(f'{BASE}/xapi/v1/Users/Pbx.RegeneratePasswords', status_code=204)
    gui.reset_user_password(144)
    body = requests_mock.last_request.json()
    assert body['Ids'] == [144]
    assert body['opts'] == {
        'WebclientPassword': True, 'SendWelcomeEmail': True, 'SipAuth': False,
        'VoicemailPIN': False, 'DeskphonePassword': False, 'RpsKey': False,
        'ConfigurationLink': False,
    }


def test_regenerate_user_passwords_error(gui, requests_mock):
    requests_mock.post(f'{BASE}/xapi/v1/Users/Pbx.RegeneratePasswords', status_code=400, text='bad')
    with pytest.raises(threecx_automata.HttpError):
        gui.regenerate_user_passwords([1], sip_auth=True)


def test_send_welcome_email(gui, requests_mock):
    requests_mock.post(f'{BASE}/xapi/v1/Users(144)/Pbx.SendWelcomeEmail', status_code=204)
    gui.send_welcome_email(144)
    assert requests_mock.called


def test_refresh_on_401(gui, requests_mock):
    '''An expired auth token is refreshed once using the refresh token cookie.'''
    requests_mock.get(f'{BASE}/xapi/v1/HotelServices', [
        {'status_code': 401},
        {'json': {'Enabled': False}},
    ])
    requests_mock.post(f'{BASE}/connect/token', json={'access_token': 'new'})

    assert gui.fetch_hotel_services() == {'Enabled': False}
    token_request = requests_mock.request_history[1]
    assert 'RefreshTokenCookie=refresh' in token_request.headers['Cookie']
    assert requests_mock.last_request.headers['Authorization'] == 'Bearer new'


def test_http_error_includes_body(gui, requests_mock):
    '''Server error details end up in the exception message.'''
    requests_mock.get(f'{BASE}/xapi/v1/SyslogSettings', status_code=403, text='Forbidden')
    with pytest.raises(threecx_automata.HttpError, match=r'403.*Forbidden'):
        gui._fetch('SyslogSettings')
