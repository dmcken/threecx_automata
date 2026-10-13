'''Read-only checks against a real 3CX server.

Skipped unless a .env file provides DOMAIN, USERNAME and PASSWORD.
'''
# pylint: disable=redefined-outer-name
import inspect

import dotenv
import pytest

import threecx_automata

CONFIG = dotenv.dotenv_values()

pytestmark = pytest.mark.skipif(
    not all(CONFIG.get(k) for k in ('DOMAIN', 'USERNAME', 'PASSWORD')),
    reason='no live server configured in .env',
)

# fetch_* methods that need no arguments.
NO_ARG_FETCHES = [
    name for name, fn in inspect.getmembers(threecx_automata.ThreeCX, inspect.isfunction)
    if name.startswith('fetch_') and not any(
        p.default is p.empty for p in list(inspect.signature(fn).parameters.values())[1:]
        if p.kind == p.POSITIONAL_OR_KEYWORD
    )
]


@pytest.fixture(scope='module')
def pbx():
    '''Logged in instance shared by every test in the module.'''
    instance = threecx_automata.ThreeCX(CONFIG['DOMAIN'])
    instance.login(CONFIG['USERNAME'], CONFIG['PASSWORD'])
    return instance


@pytest.mark.parametrize('method', NO_ARG_FETCHES)
def test_fetch(pbx, method):
    '''Every argument free fetch succeeds.'''
    getattr(pbx, method)()


def test_fetch_by_id(pbx):
    '''Fetches that take an id or name accept ones listed by other fetches.'''
    users = pbx.fetch_users()
    pbx.fetch_user_routes([u['Id'] for u in users])
    pbx.fetch_phone_registrars([p['MacAddress'] for u in users for p in u['Phones']])
    pbx.fetch_peer(users[0]['Number'])
    pbx.fetch_group(pbx.fetch_groups()[0]['Id'])
    pbx.fetch_phone_template(pbx.fetch_phone_templates()[0]['Id'])
    pbx.fetch_prompts(pbx.fetch_active_prompt_set()['Id'], top=1)
    pbx.fetch_parameter(pbx.fetch_parameters(top=1)[0]['Name'])


def test_restored_tokens_refresh(pbx):
    '''A session restored from dumped tokens recovers from a stale auth token.'''
    restored = threecx_automata.ThreeCX(
        CONFIG['DOMAIN'], tokens={**pbx.dump_tokens(), 'auth_token': 'stale'},
    )
    assert restored.fetch_version_type()
