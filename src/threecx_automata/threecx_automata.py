'''Core 3CX Web GUI Auth'''

# System
import datetime
import logging

# External
import fake_useragent
import requests

logger = logging.getLogger(__name__)

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

        token_result = self._session.post(
            url=self._build_url('/connect/token'),
            data={
                'client_id': 'Webclient',
                'grant_type': 'refresh_token',
            },
            headers=self._build_headers(False),
            timeout=self._timeout,
        )
        result_roken_json = token_result.json()
        self._auth_token = result_roken_json['access_token']

        logger.debug(f"Tokens:\n{self._access_token}\n" +\
            f"{self._refresh_token}\n{self._auth_token}")

        return True

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
        logging.getLogger("urllib3").setLevel(logging.DEBUG)

        result = self._session.get(
            url=self._build_url('/xapi/v1/Backups'),
            params={
                '$top': 50,
                "$skip": 0,
                # The space after CreationTime is causing issues (should be
                # encoded as %20 but is coming through as +).
                #"$ordeby": urllib.parse.quote("CreationTime desc", safe=""),
                "$select": "CreationTime,Size,FileName,DownloadLink",
            },
            headers=self._build_headers(),
            timeout=self._timeout,
        )
        self._display_debug(result)

        if result.status_code not in [200]:
            raise HttpError(
                "Invalid HTTP status when pulling backup list in: " +
                f"{result.status_code}"
            )

        raw_json = result.json()['value']
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

        result = self._session.post(
            url=self._build_url('/xapi/v1/Backups/Pbx.Backup'),
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
            headers=self._build_headers(),
            timeout=self._timeout,
        )

        if result.status_code not in [200,204]:
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
        result = self._session.delete(
            url=self._build_url(f"/xapi/v1/Backups('{filename}')"),
            headers=self._build_headers(),
            timeout=self._timeout,
        )
        if result.status_code not in [204]:
            raise HttpError(f"Invalid HTTP code '{result.status_code}' deleting backup")

    def fetch_status(self, raw_json: bool = True) -> str|dict[str,str|dict]:
        '''Fetch 3CX system status.


        https://<domain>/xapi/v1/SystemStatus
        '''
        result = self._session.get(
            url=self._build_url('/xapi/v1/SystemStatus'),
            headers=self._build_headers(),
            timeout=self._timeout,
        )
        # self._display_debug(result)

        if result.status_code not in [200]:
            raise HttpError(
                "Invalid HTTP status when pulling backup list in: " +
                f"{result.status_code}"
            )

        if raw_json:
            return result.text

        result_data = result.json()
        return result_data

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
