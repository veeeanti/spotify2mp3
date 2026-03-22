import os
import sys
import tekore as tk
from flask import Flask, request, redirect
import threading
import multiprocessing
import webbrowser
from time import sleep

import requests
import json

from const import colours

spotify = tk.Spotify()

userToken = None  # User token
cred = None  # credentials object for doing token ops
auths = {}  # Auth attempts. Stores data across spotify login
flask_process = None

cfg_filename = 'tekore_cfg.ini'
app_host = "127.0.0.1"
app_port = 5000
app_url = f'http://{app_host}:{app_port}'
login_redirect_url = f'{app_url}/callback'

client_prompt = f"""
{colours.FAIL}NOTE: {colours.ENDC}Your Spotify username or password will never be requested and should not be entered!
{colours.OKBLUE}
To access Spotify, you need to create a Spotify 'application' in your account.
This Spotify application gives this tool access to spotify on your behalf. You may revoke this access at any time.

1. Open {colours.OKCYAN}https://accounts.spotify.com{colours.OKBLUE} in your browser and login.
2. Visit {colours.OKCYAN}https://developer.spotify.com/dashboard/create{colours.OKBLUE} to create an application (only do this once!).
3. Provide any name and description you want.
4. For 'Redirect Uri', paste this url: {colours.OKCYAN}{login_redirect_url}{colours.OKBLUE}

Click Save.

Click on your new application from the dashboard, then click "Settings".

You will find values for "Client ID" and "Client Secret".{colours.ENDC}
"""
permission_prompt = f"""
{colours.OKBLUE}This tool will now request permission to access your Spotify application.
A browser will open requesting permission. You will see the name of the application listed. Click 'Agree'.

Press {colours.OKGREEN}enter{colours.OKBLUE} to continue{colours.ENDC}"""


# Token in the context of a user, can access user resources such as liked songs
def get_user_token():
    (spotifyClientId, spotifyClientSecret, spotifyReturnUri,
        refreshToken) = tk.config_from_file(cfg_filename, return_refresh=True)
    cred = tk.Credentials(
        spotifyClientId, spotifyClientSecret, spotifyReturnUri)

    if refreshToken is None or refreshToken == '':
        raise ValueError('RefreshToken not available in tekore config file')

    return cred.refresh_user_token(refreshToken)


def get_client_token():
    (spotifyClientId, spotifyClientSecret, spotifyReturnUri) = tk.config_from_file(
        cfg_filename, return_refresh=False
    )

    if not spotifyClientId or not spotifyClientSecret or not spotifyReturnUri:
        raise ValueError('Client credentials not available in tekore config file')

    cred = tk.Credentials(spotifyClientId, spotifyClientSecret, spotifyReturnUri)
    return cred.request_client_token()

# Scrape a "clienttoken" from spotify.com. Short lived, not refreshable token.
def get_anon_token():
    try:
        response = requests.get(
            "https://open.spotify.com/get_access_token",
            params={"reason": "transport", "productType": "web_player"},
            headers={
                "User-Agent": "Mozilla/5.0",
                "app-platform": "WebPlayer",
                "Referer": "https://open.spotify.com/",
                "Origin": "https://open.spotify.com",
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()

        token = payload.get("accessToken")
        if not token:
            raise ValueError("Spotify did not return an anonymous access token")

        return token
    except Exception as e:
        raise ValueError(f'Could not retrieve anonymous token {e}')

def is_user_logged_in():
    if not does_config_exist():
        return False

    (spotifyClientId, spotifyClientSecret, spotifyReturnUri, refreshToken) = tk.config_from_file(cfg_filename, return_refresh=True)
    return spotifyClientId != None and spotifyClientSecret != None and spotifyReturnUri != None and refreshToken != None

def is_client_configured():
    if not does_config_exist():
        return False

    (spotifyClientId, spotifyClientSecret, spotifyReturnUri) = tk.config_from_file(cfg_filename, return_refresh=False)
    return spotifyClientId != None and spotifyClientSecret != None and spotifyReturnUri != None

def does_config_exist():
    return os.path.exists(cfg_filename)


def validate_redirect_uri_config():
    if not does_config_exist():
        return

    try:
        (_client_id, _client_secret, redirect_uri) = tk.config_from_file(cfg_filename, return_refresh=False)
    except Exception:
        return

    if redirect_uri and redirect_uri != login_redirect_url:
        print(f"{colours.WARNING}[!] Redirect URI mismatch detected.{colours.ENDC}")
        print(f"{colours.WARNING}Configured: {redirect_uri}{colours.ENDC}")
        print(f"{colours.WARNING}Expected:   {login_redirect_url}{colours.ENDC}")
        print(f"{colours.WARNING}Spotify requires an exact URI match including host, port, and path.{colours.ENDC}")

def do_user_login():
    global flask_process

    if not is_client_configured():
        do_client_login()
    else:
        retry = input(f'\n{colours.OKGREEN}Spotify access is partially configured. Would you like to continue? {colours.ENDC}y\\n: ')
        if retry != "y":
            do_client_login()

    validate_redirect_uri_config()

    input(f'{permission_prompt}')

    threading.Timer(1.25, lambda: webbrowser.open(app_url)).start()

    flask_process = multiprocessing.Process(target=start_flask)
    flask_process.start()

    while flask_process.is_alive():
        sleep(1)

    print(f'\n\n{colours.OKGREEN}Success! Login complete.{colours.ENDC}')
    input(f'{colours.OKBLUE}Press {colours.OKGREEN}enter{colours.OKBLUE} to test the connection{colours.ENDC}')

    # Test login
    try:
        userToken = get_user_token()
        spotify = tk.Spotify(userToken)
        topTracks = spotify.current_user_top_tracks()

        item = topTracks.items[0]
        print(f'\n{colours.OKBLUE}It worked! {colours.ENDC}Your Top Track is: {item.name} by {item.artists[0].name}\n\n')
    except tk.HTTPError as e:
        if is_client_configured():
            os.remove(cfg_filename)
        print(f'{colours.FAIL}Something went wrong. Your credentials have been reset. Try again.{colours.ENDC}{e}')
        sys.exit(1)



def do_client_login():
    # Erase config
    if is_client_configured():
        os.remove(cfg_filename)

    print(f'{client_prompt}')

    # ClientId input
    client_id = input(f"{colours.OKGREEN}Enter Client ID{colours.ENDC}: ").strip()

    while client_id == None or client_id == '':
        client_id = input(f"{colours.WARNING}Enter a valid value! {colours.OKGREEN}Enter Client ID{colours.ENDC}: ").strip()

    # ClientSecret input
    client_secret = input(f"{colours.OKGREEN}Enter Client Secret{colours.ENDC}: ").strip()

    while client_secret == None or len(client_secret) == 0:
        client_secret = input(f"{colours.WARNING}Enter a valid value! {colours.OKGREEN}Enter Client Secret{colours.ENDC}: ").strip()

    new_conf = (client_id, client_secret, login_redirect_url, None)
    tk.config_to_file(cfg_filename, new_conf)

    print(f'{colours.OKCYAN}Saved!{colours.ENDC}')

def start_flask():
    flask_app = app_factory()
    flask_app.run(app_host, app_port)

def stop_flask():
    pid = os.getpid()
    os.kill(pid, 9) # The second argument is the signal, 9 stands for SIGKILL.

def app_factory() -> Flask:
    app = Flask(__name__)
    app.config['SECRET_KEY'] = __name__

    @app.route('/', methods=['GET'])
    def main():
        (spotifyClientId, spotifyClientSecret, spotifyReturnUri) = tk.config_from_file(cfg_filename, return_refresh=False)

        if spotifyReturnUri and spotifyReturnUri != login_redirect_url:
            print(f"{colours.WARNING}[!] Adjusting configured redirect URI for this session.{colours.ENDC}")
            print(f"{colours.WARNING}Configured: {spotifyReturnUri}{colours.ENDC}")
            print(f"{colours.WARNING}Expected:   {login_redirect_url}{colours.ENDC}")
            spotifyReturnUri = login_redirect_url

        cred = tk.Credentials(spotifyClientId, spotifyClientSecret, spotifyReturnUri)

        auth = tk.UserAuth(cred, tk.scope.read)
        auths[auth.state] = auth
        return redirect(auth.url, 307)

    @app.route('/callback', methods=['GET'])
    def login_callback():
        code = request.args.get('code', None)
        state = request.args.get('state', None)
        auth = auths.pop(state, None)

        if auth is None:
            return 'Invalid login state!', 400

        userToken = auth.request_token(code, state)

        # Store refresh token
        (existingClientId, existingClientSecret, existingReturnUri, _existingRefreshToken) = tk.config_from_file(cfg_filename, return_refresh=True)
        redirect_uri_to_store = existingReturnUri if existingReturnUri else login_redirect_url
        new_conf = (existingClientId, existingClientSecret, redirect_uri_to_store, userToken.refresh_token)
        tk.config_to_file(cfg_filename, new_conf)

        return redirect('/complete')
    
    @app.route('/complete', methods=['GET'])
    def login_complete():
        threading.Timer(3, stop_flask).start() # wait long enough to return the html

        return """
        <h1>Login complete!</h1>
        <br>
        <h4>Close this window to return to the application</h4>
        <script>window.close()</script>
        """


    return app
