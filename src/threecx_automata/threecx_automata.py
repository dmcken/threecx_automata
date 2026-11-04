'''Core 3CX Web GUI Auth

Most fetch_* methods accept the OData query options as keyword arguments,
without the leading '$': select, filter, expand, orderby, top, skip and
count. A method's default for an option (e.g. a default expand) can be
dropped by passing None for it.
'''

# System
import datetime
import logging
from typing import Any

# External
import fake_useragent
import requests

logger = logging.getLogger(__name__)

ODATA_OPTIONS = ('select', 'filter', 'expand', 'orderby', 'top', 'skip', 'count')

# Groups named like this are per-user favourites lists the web UI never shows.
_NOT_FAVORITES = "not startsWith(Name,'___FAVORITES___')"
_GROUPS_EXPAND = f"Groups($select=GroupId,Name;$filter={_NOT_FAVORITES})"

class HttpError(RuntimeError):
    '''HTTP Errors'''

class InterfaceError(RuntimeError):
    '''GUI Errors'''

class ThreeCX:
    '''3CX GUI main class'''

    def __init__(self, domain: str, ssl: bool = True,
                 tokens: dict[str,str] | None = None):
        """Constructor.

        Args:
            domain (str): Domain name of server.
            ssl (bool, optional): Is the domain SSL enabled. Defaults to True.
        """
        domain = domain.strip()
        if domain[-1] == '/':
            # Strip trailing slash, if present
            domain = domain[:-1]
        self._domain = domain
        self._ssl = ssl

        # General initialization
        ua = fake_useragent.UserAgent()
        self._user_agent_str = ua.firefox
        self._username = None
        self._password = None
        self._cookie_jar = None
        if tokens:
            self._access_token = tokens['access_token']
            self._refresh_token = tokens['refresh_token']
            self._auth_token = tokens['auth_token']
        else:
            self._access_token = None
            self._refresh_token = None
            self._auth_token = None
        self._session = requests.Session()
        self._timeout = 60 # 1 minute

    def dump_tokens(self) -> dict[str,str|None]:
        """Dump login tokens for use later on.

        Returns:
            dict[str,str|None]: Dictionary of tokens to save for later use.
        """
        return {
            'access_token': self._access_token,
            'refresh_token': self._refresh_token,
            'auth_token': self._auth_token,
        }

    def _build_url(self, relative_url: str) -> str:
        """Build absolute URL from a relative path.

        Args:
            relative_url (str): Relative URL to build upon.

        Returns:
            str: _description_
        """
        return ("https" if self._ssl else "http") + f"://{self._domain}" +\
            ('' if relative_url[0] == '/' else '/') + relative_url


    def _build_headers(self, include_auth: bool = True) -> dict[str,str]:
        """Build headers for use in requests.

        Returns:
            dict[str,str]: Headers dictionary.
        """
        temp = {
            'Accept': 'application/json',
            'User-Agent': self._user_agent_str,
        }
        if include_auth is True:
            temp['Authorization'] = f"Bearer {self._auth_token}"
        return temp

    def _display_debug(self, response):
        """Display debug data of a response.

        Args:
            response (_type_): _description_
        """
        logger.debug("REQUEST")
        logger.debug(response.request.method, response.request.url)
        logger.debug(response.request.headers)
        logger.debug(response.request.body)

        logger.debug("\nRESPONSE")
        logger.debug(response.status_code)
        logger.debug(response.headers)
        logger.debug(response.text)

    def _request(self, method: str, path: str, expected: tuple[int, ...] = (200,),
                 **kwargs) -> requests.Response:
        """Make an authenticated request, refreshing the auth token once on a 401.

        Args:
            method (str): HTTP method.
            path (str): Relative URL.
            expected (tuple[int, ...], optional): Acceptable status codes.
                Defaults to (200,).
            **kwargs: Passed through to requests (params, json, ...).

        Raises:
            HttpError: Response status not in expected.

        Returns:
            requests.Response: The response.
        """
        result = None
        for attempt in range(2):
            result = self._session.request(
                method,
                self._build_url(path),
                headers=self._build_headers(),
                timeout=self._timeout,
                **kwargs,
            )
            if result.status_code != 401 or attempt or not self._refresh_token:
                break
            logger.debug("Got 401, refreshing auth token")
            self.refresh_auth_token()

        if result.status_code not in expected:
            raise HttpError(
                f"Invalid HTTP status {result.status_code} on {method} {path}: {result.text[:200]}"
            )
        return result

    @staticmethod
    def _odata_params(defaults: dict[str, Any] | None, odata: dict[str, Any]) -> dict[str, Any]:
        """Merge OData query options over a method's defaults.

        Args:
            defaults (dict[str, Any] | None): The method's default options.
            odata (dict[str, Any]): Caller supplied options, None drops a default.

        Raises:
            TypeError: Unknown option.

        Returns:
            dict[str, Any]: Query parameters with '$' prefixed names.
        """
        unknown = set(odata) - set(ODATA_OPTIONS)
        if unknown:
            raise TypeError(f"Unknown OData option(s): {', '.join(sorted(unknown))}")

        merged = {**(defaults or {}), **odata}
        params = {}
        for name, value in merged.items():
            if value is None:
                continue
            if isinstance(value, bool):
                value = str(value).lower()
            params[f'${name}'] = value
        return params

    @staticmethod
    def _unwrap(data: Any) -> Any:
        """Strip the OData envelope from a response body.

        Collections and scalars come back as {'value': ...}, entities carry
        '@odata.*' metadata keys.
        """
        if not isinstance(data, dict):
            return data
        if 'value' in data:
            return data['value']
        return {k: v for k, v in data.items() if not k.startswith('@odata.')}

    def _fetch(self, path: str, defaults: dict[str, Any] | None = None,
               **odata: Any) -> Any:
        """GET an xapi resource and unwrap it.

        Args:
            path (str): Path relative to /xapi/v1/.
            defaults (dict[str, Any] | None, optional): Default OData options.
            **odata: OData options, see ODATA_OPTIONS.

        Returns:
            Any: List for collections, dict for entities, else a scalar.
        """
        result = self._request(
            'GET',
            f'/xapi/v1/{path}',
            params=self._odata_params(defaults, odata),
        )
        return self._unwrap(result.json())

    def _post_fetch(self, path: str, payload: dict[str, Any]) -> Any:
        """POST to an xapi function that only reads data and unwrap the result."""
        result = self._request('POST', f'/xapi/v1/{path}', json=payload)
        return self._unwrap(result.json())

    def login(self, username: str, password: str) -> bool:
        """Login to 3CX server.

        Args:
            username (str): Username to login as.
            password (str): Password to login as.

        Raises:
            RuntimeError: _description_
            RuntimeError: _description_
            RuntimeError: _description_

        Returns:
            bool: True if we successfully logged in, False otherwise.


        At the end of a successful login 3 values are set:
        - _access_token
        - _refresh_token
        - _auth_token
        """
        self._username = username
        self._password = password

        # TODO: Add checks if the tokens are set (and valid)

        result = self._session.post(
            url=self._build_url('/webclient/api/Login/GetAccessToken'),
            json={
                "ReCaptchaResponse": None,
                "SecurityCode":"",
                "Password":self._password,
                "Username":self._username,
            },
            headers=self._build_headers(False),
            timeout=self._timeout,
        )
        if result.status_code not in [200]:
            raise HttpError(f"Invalid HTTP status when logging in: {result.status_code}")

        result_json = result.json()
        if result_json['Status'] not in ['AuthSuccess']:
            raise InterfaceError(f"Unknown 3CX status when logging in: {result_json['Status']}")

        if result_json['Token']['token_type'] not in ['Bearer']:
            raise InterfaceError(f"Unknown token type: {result_json['Token']['token_type']}")

        self._access_token = result_json['Token']['access_token']
        self._refresh_token = result_json['Token']['refresh_token']

        self.refresh_auth_token()

        logger.debug(f"Tokens:\n{self._access_token}\n" +\
            f"{self._refresh_token}\n{self._auth_token}")

        return True

    def refresh_auth_token(self) -> str:
        """Fetch a new auth token (valid for an hour) using the refresh token.

        Raises:
            HttpError: The refresh token was rejected.

        Returns:
            str: The new auth token.
        """
        # The server reads the refresh token from this cookie. Login sets it,
        # but a session restored from dumped tokens has to set it here.
        if 'RefreshTokenCookie' not in self._session.cookies:
            self._session.cookies.set(
                'RefreshTokenCookie', self._refresh_token, path='/connect/token',
            )

        token_result = self._session.post(
            url=self._build_url('/connect/token'),
            data={
                'client_id': 'Webclient',
                'grant_type': 'refresh_token',
            },
            headers=self._build_headers(False),
            timeout=self._timeout,
        )
        if token_result.status_code not in [200]:
            raise HttpError(
                f"Invalid HTTP status when refreshing auth token: {token_result.status_code}"
            )
        self._auth_token = token_result.json()['access_token']
        return self._auth_token

    def backup_fetch_list(self, fname_filter: str | None = None) -> dict:
        """Fetch the backup list.

        Args:
            fname_filter (str, optional): Filter the list by the filename
                specified. Defaults to None.

        Raises:
            RuntimeError: _description_

        Returns:
            dict: _description_
        """
        raw_json = self._fetch(
            'Backups',
            orderby='CreationTime desc',
            select='CreationTime,Size,FileName,DownloadLink',
        )
        if fname_filter is not None:
            # Only return the entry matching that entry if it exists
            output = list(filter(lambda x: x['FileName'] == fname_filter, raw_json))
            return output
        return raw_json

    def backup_start(self, out_filename: str | None = None) -> str:
        """Trigger a new backup.

        Args:
            out_filename (str, optional): The user can specify the desired
                filename, otherwise one is autogenerated from the current
                UTC date. Defaults to None.

        Raises:
            RuntimeError: Invalid status code.

        Returns:
            str: Filename actually used for the backup.
        """
        if out_filename is None:
            today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
            filename = f"CDRDump-{today}.zip"
        else:
            #  TODO: Add sanity checks.
            filename = out_filename

        result = self._request(
            'POST',
            '/xapi/v1/Backups/Pbx.Backup',
            expected=(200, 204, 400),
            json={
                'description': {
                    "Name": filename,
                    "Contents":{
                        "Recordings": False,
                        "EncryptBackup": False,
                        "FQDN": True,
                        "CallHistory": True,
                        "License": True,
                        "PhoneProvisioning": True,
                        "Prompts": True,
                        "VoiceMails": True,
                        "DisableBackupCompression": False,
                    },
                },
            },
        )

        if result.status_code in [400]:
            data = result.json()
            if data['error']['details'][0]['message'] == "WARNINGS.XAPI.DUPLICATE":
                logger.error("Duplicate backup detected")
                return filename
            raise HttpError(
                "Invalid HTTP status when creating backup: " +
                f"{result.status_code} -> {result.text}"
            )

        return filename

    def backup_download(self, download_link: str, output_file: str):
        """Download a backup from the 3CX server.

        Args:
            download_link (str): URL to download from.
            output_file (str): Output file to save download to.
        """
        # This works without auth???
        with requests.get(
                    self._build_url(download_link),
                    stream=True,
                    timeout=self._timeout,
                ) as r:
            r.raise_for_status()  # fail fast on HTTP errors
            with open(output_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:  # filters out keep-alive chunks
                        f.write(chunk)

    def backup_delete(self, filename: str):
        """Delete a backup.

        Args:
            filename (str): _description_

        https://<domain>/xapi/v1/Backups('CDRDump-2026-01-19.zip')
        """
        self._request('DELETE', f"/xapi/v1/Backups('{filename}')", expected=(204,))

    def fetch_status(self, raw_json: bool = True) -> str|dict[str,str|dict]:
        '''Fetch 3CX system status.


        https://<domain>/xapi/v1/SystemStatus
        '''
        result = self._request('GET', '/xapi/v1/SystemStatus')

        if raw_json:
            return result.text

        result_data = result.json()
        return result_data

    # System / dashboard

    def fetch_system_parameters(self) -> dict:
        '''Version, FQDN, ports and hosting mode.'''
        return self._fetch('Defs/Pbx.GetSystemParameters()')

    def fetch_system_health(self) -> dict:
        '''Firewall, trunk and phone health summary.'''
        return self._fetch('SystemStatus/Pbx.SystemHealthStatus()')

    def fetch_database_information(self) -> dict:
        '''Space used by calls, chats and the event log.'''
        return self._fetch('SystemStatus/Pbx.SystemDatabaseInformation()')

    def fetch_version_type(self) -> str:
        '''Update channel of the installed version, e.g. 'Release'.'''
        return self._fetch('SystemStatus/Pbx.GetVersionType()')['Type']

    def fetch_remote_access_status(self) -> str:
        '''3CX support remote access status.'''
        return self._fetch('SystemStatus/Pbx.GetRemoteAccessStatus()')

    def fetch_license_status(self, **odata: Any) -> dict:
        '''License key, edition, expiry and reseller.'''
        return self._fetch('LicenseStatus', **odata)

    def fetch_license_parameters(self) -> dict:
        '''Product code, country and maintenance state of the license.'''
        return self._fetch('Defs/Pbx.GetLicenseParameters()')

    def fetch_updates(self) -> dict:
        '''Available PBX and phone firmware updates.'''
        return self._fetch('GetUpdates()')

    def fetch_updates_stats(self) -> dict:
        '''Pending update counts.'''
        return self._fetch('GetUpdatesStats()')

    def fetch_event_logs(self, **odata: Any) -> list[dict]:
        '''Event log entries, newest first.

        e.g. filter="Type eq 'Warning' or Type eq 'Error'", top=50
        '''
        return self._fetch('EventLogs', {'orderby': 'TimeGenerated desc'}, **odata)

    def event_logs_download(self, output_file: str, log_filter: str | None = None):
        '''Download the event log as CSV.

        Args:
            output_file (str): File to save the CSV to.
            log_filter (str | None, optional): OData filter. Defaults to all entries.
        '''
        # The UI passes the token as a query parameter here, but the header works too.
        result = self._request(
            'GET',
            '/xapi/v1/EventLogs/Pbx.DownloadEventLogs()',
            params=self._odata_params(None, {'filter': log_filter}),
        )
        with open(output_file, 'wb') as f:
            f.write(result.content)

    # Users, groups and call routing

    def fetch_users(self, **odata: Any) -> list[dict]:
        '''Users (extensions), including hot desking and AI agent extensions.

        e.g. filter="not AIAgent and not startsWith(Number,'HD')" for the
        list shown on the Users page.
        '''
        return self._fetch('Users', {
            'orderby': 'Number',
            'expand': f'Groups($filter={_NOT_FAVORITES};$expand=Rights),Phones',
        }, **odata)

    def fetch_ai_agents(self, **odata: Any) -> list[dict]:
        '''AI agent extensions.'''
        return self.fetch_users(**{'filter': 'AIAgent', **odata})

    def fetch_user_routes(self, user_ids: list[int]) -> list[dict]:
        '''DID routes (trunk and number) assigned to the given user ids.'''
        return self._post_fetch('Defs/Pbx.GetRoutes', {'ids': user_ids})

    def regenerate_user_passwords(self, user_ids: list[int], *,
                                  webclient_password: bool = False,
                                  send_welcome_email: bool = False,
                                  sip_auth: bool = False,
                                  voicemail_pin: bool = False,
                                  deskphone_password: bool = False,
                                  rps_key: bool = False,
                                  configuration_link: bool = False) -> None:
        """Regenerate the chosen credentials of the given users (by user Id,
        not extension number). Changes the PBX.

        Args:
            user_ids (list[int]): User Ids, e.g. from fetch_users().
            webclient_password (bool): A new web client / app password.
            send_welcome_email (bool): Email the user their (new) details.
            sip_auth (bool): New SIP credentials -- the user's phones and
                apps stop registering until they're set up again.
            voicemail_pin (bool): A new voicemail PIN.
            deskphone_password (bool): A new desk phone web password.
            rps_key (bool): A new provisioning (RPS) key.
            configuration_link (bool): A new provisioning link.
        """
        self._request('POST', '/xapi/v1/Users/Pbx.RegeneratePasswords', expected=(200, 204), json={
            'Ids': list(user_ids),
            'opts': {
                'WebclientPassword': webclient_password,
                'SendWelcomeEmail': send_welcome_email,
                'SipAuth': sip_auth,
                'VoicemailPIN': voicemail_pin,
                'DeskphonePassword': deskphone_password,
                'RpsKey': rps_key,
                'ConfigurationLink': configuration_link,
            },
        })

    def reset_user_password(self, user_id: int) -> None:
        """A new web client password for the user, emailed to them -- the
        Users page's "Reset password". Changes the PBX."""
        self.regenerate_user_passwords([user_id], webclient_password=True, send_welcome_email=True)

    def send_welcome_email(self, user_id: int) -> None:
        """Re-send the user's welcome email, changing nothing."""
        self._request(
            'POST', f'/xapi/v1/Users({int(user_id)})/Pbx.SendWelcomeEmail', expected=(200, 204),
        )

    def fetch_phone_registrars(self, macs: list[str]) -> list[dict]:
        '''Where the phones with the given MAC addresses are registered.'''
        # Phones without a MAC (e.g. SBC provisioned) are rejected by the server.
        return self._post_fetch('Users/Pbx.GetPhoneRegistrars', {'macs': [m for m in macs if m]})

    def fetch_groups(self, include_favorites: bool = False, **odata: Any) -> list[dict]:
        '''Groups (departments).

        Args:
            include_favorites (bool, optional): Include the hidden per-user
                favourites groups. Defaults to False.
        '''
        if not include_favorites:
            user_filter = odata.get('filter')
            odata['filter'] = f'({user_filter}) and {_NOT_FAVORITES}' if user_filter \
                else _NOT_FAVORITES
        return self._fetch('Groups', {'orderby': 'Name'}, **odata)

    def fetch_group(self, group_id: int, **odata: Any) -> dict:
        '''A single group, including office hours and holidays.'''
        return self._fetch(f'Groups({group_id})', {'expand': 'OfficeHolidays'}, **odata)

    def fetch_ring_groups(self, **odata: Any) -> list[dict]:
        '''Ring groups.'''
        return self._fetch('RingGroups', {'expand': _GROUPS_EXPAND}, **odata)

    def fetch_queues(self, **odata: Any) -> list[dict]:
        '''Call queues.'''
        return self._fetch('Queues', {'expand': _GROUPS_EXPAND}, **odata)

    def fetch_receptionists(self, **odata: Any) -> list[dict]:
        '''Digital receptionists (IVRs).'''
        return self._fetch('Receptionists', {'expand': _GROUPS_EXPAND}, **odata)

    def fetch_call_flow_apps(self, **odata: Any) -> list[dict]:
        '''Call flow apps.'''
        return self._fetch('CallFlowApps', {
            'orderby': 'Number',
            'expand': f'{_GROUPS_EXPAND},Trunk($select=Gateway)',
        }, **odata)

    def fetch_peer(self, number: str) -> dict:
        '''Look up whatever owns an extension number (user, queue, IVR ...).'''
        return self._fetch(f"Peers(Number='{number}')")

    def fetch_website_links(self, **odata: Any) -> list[dict]:
        '''Website (click to talk) links.'''
        return self._fetch('WebsiteLinks', {'filter': 'Hidden eq false', 'expand': 'DN'}, **odata)

    def fetch_contacts(self, **odata: Any) -> list[dict]:
        '''Company phonebook contacts.'''
        return self._fetch('Contacts', **odata)

    def fetch_time_zones(self) -> list[dict]:
        '''Time zones selectable for office hours.'''
        return self._fetch('Defs/TimeZones')

    def fetch_office_hours(self, **odata: Any) -> dict:
        '''System wide office hours, time zone and language.'''
        return self._fetch('OfficeHours', **odata)

    # Trunks and rules

    def fetch_trunks(self, **odata: Any) -> list[dict]:
        '''SIP trunks, bridges and messaging trunks.'''
        return self._fetch('Trunks', {'expand': f'RoutingRules,{_GROUPS_EXPAND}'}, **odata)

    def fetch_inbound_rules(self, **odata: Any) -> list[dict]:
        '''Inbound (DID and caller ID) rules.'''
        return self._fetch('InboundRules', {'expand': 'TrunkDN'}, **odata)

    def fetch_outbound_rules(self, **odata: Any) -> list[dict]:
        '''Outbound rules, in priority order.'''
        return self._fetch('OutboundRules', {'orderby': 'Priority'}, **odata)

    def fetch_emergency_outbound_rules(self, **odata: Any) -> list[dict]:
        '''Emergency number outbound rules.'''
        return self._fetch('OutboundRules/Pbx.GetEmergencyOutboundRules()',
                           {'orderby': 'Priority'}, **odata)

    def fetch_blacklist_numbers(self, **odata: Any) -> list[dict]:
        '''Blocked caller IDs.'''
        return self._fetch('BlackListNumbers', **odata)

    # Phones and devices

    def fetch_sip_devices(self, **odata: Any) -> list[dict]:
        '''Provisioned SIP devices (phones) and the DN they belong to.'''
        return self._fetch('SipDevices', {'expand': 'DN'}, **odata)

    def fetch_sbcs(self, **odata: Any) -> list[dict]:
        '''Session border controllers.'''
        return self._fetch('Sbcs', **odata)

    def fetch_fxs(self, **odata: Any) -> list[dict]:
        '''FXS / DECT gateways.'''
        return self._fetch('Fxs', {'expand': 'Template($select=DeviceType),Registered'}, **odata)

    def fetch_firmwares(self, **odata: Any) -> list[dict]:
        '''Phone firmware files on the PBX.'''
        return self._fetch('Firmwares', **odata)

    def fetch_firmware_state(self) -> dict:
        '''Count and total size of stored firmware files.'''
        return self._fetch('Firmwares/Pbx.GetFirmwareState()')

    def fetch_phones_settings(self) -> dict:
        '''Global phone provisioning settings.'''
        return self._fetch('PhonesSettings')

    def fetch_phone_templates(self, **odata: Any) -> list[dict]:
        '''Phone provisioning templates.'''
        return self._fetch('PhoneTemplates', {
            'filter': "TemplateType ne 'Deleted'",
            'orderby': 'IsCustom, TemplateType',
        }, **odata)

    def fetch_phone_template(self, template_id: str) -> str:
        '''XML content of a phone template, e.g. 'fanvil_doorphone.ph.xml'.'''
        return self._fetch(f"PhoneTemplates('{template_id}')", select='Content')['Content']

    def fetch_faxes(self, **odata: Any) -> list[dict]:
        '''Fax extensions (excluding the fax server).'''
        return self._fetch('Fax', {'filter': 'FaxServer ne true'}, **odata)

    def fetch_fax_files_size(self) -> int:
        '''Bytes used by stored faxes.'''
        return self._fetch('FaxServerSettings/Pbx.GetFaxFilesSize()')

    # Integrations

    def fetch_crm_integration(self, **odata: Any) -> dict:
        '''CRM integration settings.'''
        return self._fetch('CrmIntegration', {'expand': 'PossibleValues'}, **odata)

    def fetch_ai_resources(self) -> dict:
        '''AI models, voices and templates available to AI agents.'''
        return self._fetch('AISettings/Pbx.GetAIResources()')

    def fetch_google_settings(self, **odata: Any) -> dict:
        '''Google sign-in and user sync settings.'''
        return self._fetch('GoogleSettings', **odata)

    def fetch_amazon_integration_settings(self) -> dict:
        '''Amazon S3 integration settings.'''
        return self._fetch('AmazonIntegrationSettings')

    def fetch_microsoft365_directory(self) -> dict:
        '''Microsoft 365 directory sync settings.'''
        return self._fetch('Microsoft365Integration/Pbx.GetMicrosoft365Directory()')

    def fetch_microsoft365_teams_integration(self) -> dict:
        '''Microsoft Teams direct routing settings.'''
        return self._fetch('Microsoft365TeamsIntegration')

    def fetch_hotel_services(self) -> dict:
        '''Hotel module (PMS) settings.'''
        return self._fetch('HotelServices')

    def fetch_data_connector_settings(self) -> dict:
        '''Data connector (offload to external database) settings.'''
        return self._fetch('DataConnectorSettings')

    # Settings

    def fetch_voicemail_settings(self, **odata: Any) -> dict:
        '''Voicemail quota, retention and transcription settings.'''
        return self._fetch('VoicemailSettings', **odata)

    def fetch_transcribe_languages(self) -> list[dict]:
        '''Languages available for transcription.'''
        return self._fetch('VoicemailSettings/Pbx.GetTranscribeLanguages()')

    def fetch_general_settings_for_pbx(self) -> dict:
        '''General PBX settings (operator, call pickup, busy monitor ...).'''
        return self._fetch('GeneralSettingsForPbx')

    def fetch_general_settings_for_apps(self) -> dict:
        '''Settings applied to the 3CX apps (branding, chat ...).'''
        return self._fetch('GeneralSettingsForApps')

    def fetch_dial_code_settings(self) -> dict:
        '''Feature dial codes (park, pickup, voicemail ...).'''
        return self._fetch('DialCodeSettings')

    def fetch_call_parking_settings(self) -> dict:
        '''Call parking settings.'''
        return self._fetch('CallParkingSettings')

    def fetch_conference_settings(self) -> dict:
        '''Conferencing settings.'''
        return self._fetch('ConferenceSettings')

    def fetch_web_meeting_zones(self) -> list[str]:
        '''Web meeting zones.'''
        return self._fetch('ConferenceSettings/Pbx.GetWebMeetingZones()')

    def fetch_onboard_meetings(self) -> list[dict]:
        '''Onboard meetings.'''
        return self._fetch('ConferenceSettings/Pbx.GetOnboardMeetings()')

    def fetch_mail_settings(self) -> dict:
        '''Mail server settings, including the SMTP password.'''
        return self._fetch('MailSettings')

    def fetch_notification_settings(self) -> dict:
        '''Admin email notification settings.'''
        return self._fetch('NotificationSettings')

    def fetch_e164_settings(self) -> dict:
        '''E164 number formatting settings.'''
        return self._fetch('E164Settings')

    def fetch_cdr_settings(self) -> dict:
        '''Call detail record output settings.'''
        return self._fetch('CDRSettings')

    def fetch_country_codes(self) -> list[str]:
        '''Country dialing codes allowed for outbound calls.'''
        return self._fetch('CountryCodes')['CountryCodes']

    def fetch_countries(self, **odata: Any) -> list[dict]:
        '''Countries and their dialing codes.'''
        return self._fetch('Countries', **odata)

    def fetch_codecs_settings(self) -> dict:
        '''Local and external codec priority lists.'''
        return self._fetch('CodecsSettings')

    def fetch_parameters(self, **odata: Any) -> list[dict]:
        '''Custom (advanced) parameters.'''
        return self._fetch('Parameters', **odata)

    def fetch_parameter(self, name: str) -> dict:
        '''A single custom parameter by name.'''
        return self._fetch(f"Parameters/Pbx.GetParameterByName(name='{name}')")

    # Security and network

    def fetch_network_settings(self) -> dict:
        '''Public IP, STUN and FQDN settings.'''
        return self._fetch('NetworkSettings')

    def fetch_network_interfaces(self) -> list[str]:
        '''IP addresses of the server's network interfaces.'''
        return self._fetch('NetworkSettings/Pbx.GetIfaces()')

    def fetch_blocklist(self, **odata: Any) -> list[dict]:
        '''IP blocklist / allowlist entries.

        e.g. filter="AddedBy eq 'Manual' or AddedBy eq 'Whitelist'" for the
        entries shown in the UI.
        '''
        return self._fetch('Blocklist', **odata)

    def fetch_anti_hacking_settings(self) -> dict:
        '''Anti-hacking thresholds and blacklist duration.'''
        return self._fetch('AntiHackingSettings')

    def fetch_console_restrictions(self) -> dict:
        '''Management console IP restrictions.'''
        return self._fetch('ConsoleRestrictions')

    def fetch_secure_sip_settings(self) -> dict:
        '''Secure SIP certificate and private key.'''
        return self._fetch('SecureSipSettings')

    # Storage and backups

    def fetch_backup_settings(self) -> dict:
        '''Scheduled backup settings.'''
        return self._fetch('Backups/Pbx.GetBackupSettings()')

    def fetch_local_backup_size(self) -> int:
        '''Bytes used by local backups.'''
        return self._fetch('Backups/Pbx.GetLocalBackupSize()')

    def fetch_remote_archiving_settings(self) -> dict:
        '''Remote archiving location and per data type settings.'''
        return self._fetch('RemoteArchivingSettings')

    # Prompts and music on hold

    def fetch_prompt_sets(self, **odata: Any) -> list[dict]:
        '''Installed system prompt sets (languages).'''
        return self._fetch('PromptSets', **odata)

    def fetch_active_prompt_set(self) -> dict:
        '''Prompt set currently in use.'''
        return self._fetch('PromptSets/Pbx.GetActive()')

    def fetch_prompts(self, prompt_set_id: int, **odata: Any) -> list[dict]:
        '''Prompts within a prompt set.'''
        return self._fetch(f'PromptSets({prompt_set_id})/Prompts', **odata)

    def fetch_custom_prompts(self, **odata: Any) -> list[dict]:
        '''Uploaded custom prompts.'''
        return self._fetch('CustomPrompts', **odata)

    def fetch_playlists(self, **odata: Any) -> list[dict]:
        '''Music on hold playlists.'''
        return self._fetch('Playlists', **odata)

    def fetch_music_on_hold_settings(self) -> dict:
        '''Music on hold sources.'''
        return self._fetch('MusicOnHoldSettings')

if __name__ == '__main__':
    import pprint
    import time

    import dotenv

    logging.basicConfig(level=logging.DEBUG)

    config = dotenv.dotenv_values()
    x = ThreeCX(config['DOMAIN'])
    x.login(config['USERNAME'],config['PASSWORD'])


    x.fetch_status()

    time.sleep(10)

    transit_tokens = x.dump_tokens()

    # Save dictionary to JSON in a file and load back.

    pprint.pprint(transit_tokens)

    x1 = ThreeCX(config['DOMAIN'], tokens=transit_tokens)

    pprint.pprint(x1.fetch_status())
