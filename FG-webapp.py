""" FlightGazer web interface. Handles editing the config file,
reading the logs, checking for updates, and modifying startup options.
This won't run if FlightGazer isn't installed on the system as it depends on
FlightGazer's virtual environment. (should be handled via the install script).
Disclosures: most of this was initially vibe-coded, however at this state, it
has been manually tweaked and modified to the point it's *good enough*. """

"""
    Copyright (C) 2026, WeegeeNumbuh1.

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
    GNU General Public License for more details.

    A copy of the GNU General Public License should already exist in the
    parent directory of which this file resides in.
    If not, see <https://www.gnu.org/licenses/>.
"""

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_file,
    send_from_directory,
    make_response,
)
from ruamel.yaml import YAML
import os
import sys
import re
import json
import threading
import queue
import subprocess
import requests
import psutil
import socket
from time import sleep
import io
import datetime
import logging
import importlib.metadata
import concurrent.futures as CF

VERSION = "v.0.16.8 --- 2026-01-18"

# don't touch this, this is for proxying the webpages
os.environ['SCRIPT_NAME'] = '/flightgazer'

# Define the paths for all the files we're looking for.
# NB: It is expected that this Flask app lives inside the `web-app` folder
# in the FlightGazer directory.
CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
COLORS_PATH = os.path.join(os.path.dirname(__file__), '..', 'setup', 'colors.py')
VERSION_PATH = os.path.join(os.path.dirname(__file__), '..', 'version')
WEBAPP_VERSION_PATH = os.path.join(os.path.dirname(__file__), 'version-webapp')
LOG_PATH = os.path.join(os.path.dirname(__file__), '..', 'FlightGazer-log.log')
MIGRATE_LOG_PATH = os.path.join(os.path.dirname(__file__), '..', 'settings_migrate.log')
FLYBY_STATS_PATH = os.path.join(os.path.dirname(__file__), '..', 'flybys.csv')
CURRENT_STATE_JSON_PATH = '/run/FlightGazer/current_state.json'
BAD_STATE_SEMAPHORE = '/run/FlightGazer/not_good'
SERVICE_PATH = '/etc/systemd/system/flightgazer.service'
# Get the main FlightGazer path rather than using a relational path.
# If the latter approach is used and this web-app is uninstalled, it will break the service
# as now this working directory `web-app` no longer exists.
INIT_PATH = os.path.join(os.path.dirname(os.getcwd()), 'FlightGazer-init.sh')
UPDATE_PATH = os.path.join(os.path.dirname(__file__), '..', 'update.sh')
CURRENT_IP = '' # local IP address of the system
HOSTNAME = socket.gethostname()
RUNNING_ADSBIM = False
localpages = {}
is_posix = True if os.name == 'posix' else False
try:
    FLASK_VER = importlib.metadata.version('flask')
except Exception:
    FLASK_VER = "Unknown"

webapp_session = requests.session()

yaml = YAML()
yaml.preserve_quotes = True

main_logger = logging.getLogger("FlightGazer-webapp")
# set root logger to write out to file but not stdout
logging.basicConfig(
    stream=sys.stdout,
    format='%(asctime)s.%(msecs)03d - %(name)s %(threadName)s | %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    encoding='utf-8',
    level=logging.INFO,
)

app = Flask(__name__)
app.json.sort_keys = False
main_logger.info(f"This is the FlightGazer-webapp, version {VERSION}")
main_logger.info(f"Running from {os.getcwd()} on {HOSTNAME}")
main_logger.info(f"Flask version: {FLASK_VER}")

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

# ========= Helper Functions =========

def load_config():
    """ Load FlightGazer's config file. You must try-except this function. """
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        data = yaml.load(f)
    return data

def save_config(data):
    """ Save config and preserve owner/group. You must try-except this function """
    orig_stat = None
    if os.path.exists(CONFIG_PATH):
        orig_stat = os.stat(CONFIG_PATH)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(data, f)
    main_logger.info("Updated main configuration")
    if orig_stat and os.name != 'nt':
        os.chown(CONFIG_PATH, orig_stat.st_uid, orig_stat.st_gid)

def get_predefined_colors():
    """  Parse colors.py for color names and RGB values. You must try-except this function. """
    color_names = []
    color_rgbs = {}
    with open(COLORS_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            m = re.match(r'([A-Z_]+)\s*=\s*graphics.Color\((\d+),\s*(\d+),\s*(\d+)\)', line)
            if m:
                name = m.group(1)
                r = int(m.group(2))
                g = int(m.group(3))
                b = int(m.group(4))
                color_names.append(name)
                color_rgbs[name] = '#%02x%02x%02x' % (r,g,b)
    return color_names, color_rgbs

def get_color_config():
    """ Parse colors.py for current color assignments. You must try-except this function. """
    color_vars = [
        # Clock colors
        'clock_color','seconds_color','am_pm_color','day_of_week_color','date_color',
        'flyby_header_color','flyby_color','track_header_color','track_color','range_header_color','range_color',
        'center_row1_color','center_row2_color',
        # Plane Readout Colors
        'callsign_color','distance_color','country_color','uat_indicator_color',
        # Journey colors
        'origin_color','destination_color','arrow_color',
        # Journey plus colors
        'time_header_color','time_readout_color','center_readout_color',
        # Enhanced readout colors
        'latitude_color','longitude_color','groundtrack_color','verticalspeed_color',
        # Scrolling info line
        'marquee_color_journey_plus','marquee_color_enhanced_readout',
        # Stats at the bottom
        'altitude_heading_color','altitude_color','speed_heading_color','speed_color','time_rssi_heading_color','time_rssi_color',
        # Plane count indicator
        'plane_count_color','switch_progress_color'
    ]
    color_config = {}
    with open(COLORS_PATH, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    for var in color_vars:
        for line in lines:
            if line.strip().startswith(f'{var} ='):
                rhs = line.split('=',1)[1].strip()
                if rhs.startswith('graphics.Color'):
                    rgb = rhs[len('graphics.Color('):-1].split(',')
                    r,g,b = [int(x.strip()) for x in rgb]
                    color_config[var] = {'type':'custom','hex':'#%02x%02x%02x' % (r,g,b)}
                else:
                    color_config[var] = {'type':'predef','value':rhs}
                break
        else:
            color_config[var] = {'type':'predef','value':'WHITE'}
    return color_config

def save_color_config(colors):
    """  Update colors.py assignments for color_vars and preserve owner/group. You must try-except this function. """
    color_vars = [
        # Clock colors
        'clock_color','seconds_color','am_pm_color','day_of_week_color','date_color',
        'flyby_header_color','flyby_color','track_header_color','track_color','range_header_color','range_color',
        'center_row1_color','center_row2_color',
        # Plane Readout Colors
        'callsign_color','distance_color','country_color','uat_indicator_color',
        # Journey colors
        'origin_color','destination_color','arrow_color',
        # Journey plus colors
        'time_header_color','time_readout_color','center_readout_color',
        # Enhanced readout colors
        'latitude_color','longitude_color','groundtrack_color','verticalspeed_color',
        # Scrolling info line
        'marquee_color_journey_plus','marquee_color_enhanced_readout',
        # Stats at the bottom
        'altitude_heading_color','altitude_color','speed_heading_color','speed_color','time_rssi_heading_color','time_rssi_color',
        # Plane count indicator
        'plane_count_color','switch_progress_color'
    ]
    orig_stat = None
    if os.path.exists(COLORS_PATH):
        orig_stat = os.stat(COLORS_PATH)
    with open(COLORS_PATH, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    for i,line in enumerate(lines):
        for var in color_vars:
            if line.strip().startswith(f'{var} ='):
                c = colors.get(var)
                if c:
                    if c['type']=='predef':
                        lines[i] = f'{var} = {c["value"]}\n'
                    elif c['type']=='custom':
                        hexval = c['hex'].lstrip('#')
                        r = int(hexval[0:2],16)
                        g = int(hexval[2:4],16)
                        b = int(hexval[4:6],16)
                        lines[i] = f'{var} = graphics.Color({r}, {g}, {b})\n'
    with open(COLORS_PATH, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    main_logger.info("Updated color configuration")
    if orig_stat and os.name != 'nt':
        os.chown(COLORS_PATH, orig_stat.st_uid, orig_stat.st_gid)

def get_version() -> str:
    try:
        with open(VERSION_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return 'Unknown'

def get_webapp_version() -> str:
    try:
        with open(WEBAPP_VERSION_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return 'Unknown'

def get_ip() -> None:
    """ Gets us our local IP (and hostname). Pulled exactly as is from the main FlightGazer script.
    Modifies the globals `CURRENT_IP` and `HOSTNAME` """
    global CURRENT_IP, HOSTNAME
    ip_now = CURRENT_IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        s.connect(('10.254.254.254', 1)) # doesn't even need to connect
        IP = s.getsockname()[0]
    except Exception:
        IP = ""
    finally:
        s.close()
    if not ip_now and IP:
        main_logger.info(f"Current IP: {IP}")
    if ip_now and IP != ip_now:
        main_logger.info(f"IP address changed: {ip_now} -> {IP}")
    HOSTNAME = socket.gethostname()
    CURRENT_IP = IP

def local_webpage_prober() -> dict:
    """ Probes what webpages are available running locally on this system.
    Probes these key locations:
    - Find adsb.im
        - Either at root IP address or at port 1099
    - Find tar1090
        - Either at /tar1090 or port 8080
    - Find graphs1090
        - Either at /graphs1090 or port 8542
    - Port 8888
        - The Display Emulator
    - Find Skystats
        - Either at /skystats or port 5173
    - Port 8088
        - Planefence

    Returns a dict, with the key-value pairs being the link name and URL.
    """
    global RUNNING_ADSBIM
    pages = {}
    def webpage_title(url: str) -> tuple[str, str | None]:
        """ Get the title of a webpage given `input` as a string. """
        try:
            response = webapp_session.get(url, timeout=4, allow_redirects=True)
            # get the webpage title; https://stackoverflow.com/a/47236359
            resp_body = response.text
            title = re.search('(?<=<title>).+?(?=</title>)', resp_body, re.DOTALL)
            if title:
                return url, title.group().strip()
        except Exception:
            pass
        return url, None

    def match_title(input: None | str, title: str) -> bool:
        """ If a given `input` is found in `title`, returns True,
        False otherwise. """
        if input and title in input:
            return True
        return False

    root = f"http://{CURRENT_IP}"
    adsbim = f"http://{CURRENT_IP}:1099"
    candidates = {
        'adsb_root': [root, adsbim],
        'display_emulator': [f"http://{CURRENT_IP}:8888", "http://localhost:8888"],
        'tar1090': [f"http://{CURRENT_IP}/tar1090", f"http://{CURRENT_IP}:8080"],
        'graphs1090': [f"http://{CURRENT_IP}/graphs1090", f"http://{CURRENT_IP}:8542"],
        'skystats': [f"http://{CURRENT_IP}/skystats", f"http://{CURRENT_IP}:5173"],
        'planefence': [f"http://{CURRENT_IP}:8088"],
    }

    # flatten URL list for use with the threadpool
    urls = []
    for lst in candidates.values():
        for u in lst:
            if u not in urls:
                urls.append(u)

    # probe the stuff concurrently
    results: dict[str, None | str] = {}
    max_workers = len(urls)
    with CF.ThreadPoolExecutor(max_workers=max_workers) as exe:
        future_to_url = {exe.submit(webpage_title, u): u for u in urls}
        for fut in CF.as_completed(future_to_url):
            url, title = fut.result()
            results[url] = title

    # find adsb.im
    local_page = results.get(root)
    if local_page:
        match local_page:
            case x if "Feeder Homepage" in x:
                pages.update({"System Configuration & Management, Maps, and Stats": root})
                RUNNING_ADSBIM = True
            case x if "PiAware" in x: # FlightAware's PiAware is running here
                pages.update({"FlightAware PiAware page": root})
            case x if "SDR Setup" in x:
                pages.update({"⚠️ SDR Disconnected! Click Here to Investigate/Fix": root})
                RUNNING_ADSBIM = True
            # case _: # something else is running here, link it anyway
            #     pages.update({f"{local_page}"[:100]: root})
    else:
        local_page = results.get(adsbim)
        if match_title(local_page, "Feeder Homepage"):
            pages.update({"System Configuration & Management, Maps, and Stats": adsbim})
            RUNNING_ADSBIM = True
        elif match_title(local_page, "SDR Setup"):
            pages.update({"⚠️ SDR Disconnected! Click Here to Investigate/Fix": adsbim})
            RUNNING_ADSBIM = True

    # try to find the display emulator
    for url in candidates['display_emulator']:
        t = results.get(url)
        if t and any(x in t for x in ("FlightGazer", "RGBME")):
            pages.update({"RGBMatrixEmulator": url})
            break

    # find the rest of our stuff
    # tar1090
    for url in candidates['tar1090']:
        if match_title(results.get(url), "tar1090"):
            pages.update({"tar1090 Tracking Interface": url})
            break

    # graphs1090
    for url in candidates['graphs1090']:
        if match_title(results.get(url), "graphs1090"):
            pages.update({"graphs1090 Statistics Interface": url})
            break

    # skystats
    for url in candidates['skystats']:
        if match_title(results.get(url), "Skystats"):
            pages.update({"Skystats": url})
            break

    # planefence
    planefence_url = candidates['planefence'][0]
    if match_title(results.get(planefence_url), "Planefence"):
        pages.update({"Planefence": planefence_url})

    return pages

def match_commandline(command_search: str, process_name: str) -> int | None:
    """ Taken directly from `FlightGazer.py` but modified to only return the PID.
    This must only be used when it's known that a single instance is running. """
    list_of_processes = []
    # iterate over all running processes
    iter = 0
    for proc in psutil.process_iter():
       iter += 1
       try:
           pinfo = proc.as_dict(attrs=['pid', 'name', 'create_time'])
           cmdline = proc.cmdline()
           # check if process name contains the given string in its command line
           if any(command_search in position for position in cmdline) and process_name in pinfo['name']:
               list_of_processes.append(pinfo)
       except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
           pass

    if len(list_of_processes) >= 1:
        return list_of_processes[0]['pid']
    else:
        return None

# Use psutil to track/monitor the main running FlightGazer process instead of
# having to fire up a subprocess each time we want to poll FlightGazer's running status
current_flightgazer_pid = None
current_flightgazer_process = None
manually_running = False

def flightgazer_bad_state() -> bool:
    """ Check if FlightGazer is running in a bad state or has failed """
    return is_posix and os.path.exists(BAD_STATE_SEMAPHORE)

def get_flightgazer_status():
    """ Poll the service status """
    global current_flightgazer_pid, current_flightgazer_process, manually_running
    if not os.path.exists(SERVICE_PATH): # just for convenience when not running on linux (debugging on Windows)
        try:
            current_flightgazer_pid = match_commandline('FlightGazer.py', 'python')
            if current_flightgazer_pid is not None:
                if not current_flightgazer_process:
                    main_logger.info(f"Detected FlightGazer's PID: {current_flightgazer_pid}")
                    current_flightgazer_process = psutil.Process(pid=current_flightgazer_pid)
                manually_running = True
                return 'manually running'
            else:
                return 'stopped'
        except Exception:
            current_flightgazer_pid = None
            manually_running = False
            return 'failed'

    try:
        # if the previous poll showed that no existing PID for FlightGazer is running, run through this
        flow_reason = ""
        if current_flightgazer_process is None:
            flow_reason = "FlightGazer process does not exist"
        if current_flightgazer_process and not current_flightgazer_process.is_running():
            flow_reason = f"Previous FlightGazer process PID {current_flightgazer_pid} is no longer running"
        if flow_reason:
            result = subprocess.run(['systemctl', 'is-active', 'flightgazer'], capture_output=True, text=True)
            result2 = subprocess.run(['systemctl', 'is-failed', 'flightgazer'], capture_output=True, text=True)
            if result2.returncode == 1: # no failures
                if ((result.returncode == 0 and result.stdout.strip() == 'active')
                    or result.stdout.strip() == 'activating'):
                    main_logger.info(f"Running through process discovery logic (Reason: {flow_reason})")
                    # try to find the PID of the running instance
                    current_flightgazer_pid = match_commandline('FlightGazer.py', 'python')
                    if current_flightgazer_pid is not None:
                        main_logger.info(f"Detected FlightGazer's PID: {current_flightgazer_pid}")
                        # The main process of FlightGazer is running (not in the systemd startup phase)
                        current_flightgazer_process = psutil.Process(pid=current_flightgazer_pid)
                        manually_running = False
                    return 'running'
                elif result.returncode == 3 or result.stdout.strip() == 'inactive':
                    current_flightgazer_pid = match_commandline('FlightGazer.py', 'python')
                    if current_flightgazer_pid is not None:
                        main_logger.info(f"Detected a manual instance of FlightGazer, PID: {current_flightgazer_pid}")
                        current_flightgazer_process = psutil.Process(pid=current_flightgazer_pid)
                        manually_running = True
                        return 'manually running'
                    else:
                        current_flightgazer_process = None
                        manually_running = False
                        if flightgazer_bad_state():
                            return 'failed'
                        else:
                            return 'stopped'
                else:
                    current_flightgazer_pid = None
                    current_flightgazer_process = None
                    manually_running = False
                    return 'failed'
            else:
                # case when it is manually running and a service restart was attempted (it will always fail)
                current_flightgazer_pid = match_commandline('FlightGazer.py', 'python')
                if current_flightgazer_pid is not None:
                    main_logger.info(f"Detected a manual instance of FlightGazer, PID: {current_flightgazer_pid}")
                    current_flightgazer_process = psutil.Process(pid=current_flightgazer_pid)
                    manually_running = True
                    return 'manually running'
                else:
                    current_flightgazer_pid = None
                    current_flightgazer_process = None
                    manually_running = False
                    return 'failed'
        else:
            if flightgazer_bad_state():
                return 'degraded'
            else:
                if not manually_running:
                    return 'running'
                else:
                    return 'manually running'

    except Exception:
        main_logger.exception("Process discovery failed.")
        current_flightgazer_pid = None
        current_flightgazer_process = None
        manually_running = False
        return 'failed'

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
def remove_ansi_escape(input: str) -> str:
    """ Remove the escape sequences used in the update script.
    https://stackoverflow.com/a/14693789 """
    return ANSI_ESCAPE.sub('', input)

def append_date_now() -> str:
    """ Returns a string of the current local time, in ISO8601 format, compatible with filesystems """
    return datetime.datetime.now().strftime("%Y-%m-%dT%H%M%S")

remote_ver = None
remote_changelog = None
def update_fetcher() -> None:
    """ Checks and fetches changelog from the repo.
    Modifies the globals `remote_ver` and `remote_changelog`.
    You must encapsulate this function in a try-except block. """
    global remote_ver, remote_changelog
    # Fetch remote version and changelog
    main_logger.info("Update check requested")
    ver_file = 'https://raw.githubusercontent.com/WeegeeNumbuh1/FlightGazer/main/version'
    new_changelog = 'https://raw.githubusercontent.com/WeegeeNumbuh1/FlightGazer/main/Changelog.txt'
    if remote_ver is None:
        remote_ver = requests.get(ver_file, timeout=10).text.strip()
        remote_changelog = requests.get(new_changelog, timeout=10).text
        main_logger.info(f"Version available online: {remote_ver}")
    else:
        remote_ver_ = requests.get(ver_file, timeout=10).text.strip()
        if remote_ver_ != remote_ver:
            main_logger.info(f"Version available online: {remote_ver}")
            remote_ver = remote_ver_
            remote_changelog = requests.get(new_changelog, timeout=10).text
        else:
            main_logger.info("Cache still valid; no updates since last checked")

class ActionTimeout:
    """ Prevent abusing some control routes.
    When a control action (e.g: service start, restart, stop) is called, this
    object serves as a guard to prevent abusing these actions.
    ### how to use:
    >>> lockout_sec = 30
    >>> guard = ActionTimeout(lockout_sec)
    >>> def should_i_do_this():
    >>>      if guard.touch():
    >>>          yeah_lets_go()
    >>>      else:
    >>>          nah_bro()
    """
    def __init__(self, lockout_time: int = 30):
        self._lock = threading.Lock()
        self._lockout_time = lockout_time
        self._timer: threading.Timer | None = None
        self.good_to_go = True

    def _expire(self) -> None:
        """ Called by the timer when the lockout period ends. """
        with self._lock:
            self._timer = None
            self.good_to_go = True

    def _start_timer(self) -> None:
        """ Start or restart the background timer """
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self._lockout_time, self._expire)
        self._timer.daemon = True
        self._timer.start()
        self.good_to_go = False

    def _reset_timer(self) -> None:
        """ Cancel any running timer and allow actions immediately """
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self.good_to_go = True

    def touch(self) -> bool:
        """ Check if the guard allows an action. If allowed, start the lockout timer
        in the background and return True. If lockout is active, return False.
        This method returns immediately (non-blocking)
        """
        with self._lock:
            if self.good_to_go:
                self._start_timer()
                return True
            else:
                return False

# ========= Initialization Stuff =========
get_ip()
""" Keeps track of how many times the probing thread has done its work.
This gets reset if a command to change the running state of the main FlightGazer
service is used. """
def probing_thread() -> None:
    """ Probes available webpages: does an initial burst every
    30 seconds for 10 minutes at startup (to wait for the other pages to start),
    then once every 5 minutes for the next hour,
    then updates twice a day thereafter.
    Modifies the `localpages` global. """
    global localpages
    probing_cycle_count = 0
    stage1 = 20
    stage2 = 32
    while True:
        localpages = local_webpage_prober()
        probing_cycle_count += 1
        if probing_cycle_count < stage1:
            sleep(30)
            get_ip()
        elif stage1 <= probing_cycle_count < stage2:
            sleep(300)
            get_ip()
        else:
            sleep(43200)
            # no need to check IP, it's handled below

def update_ip() -> None:
    """ Check if the IP changes (every hour) """
    while True:
        # note we sleep first to let the probing thread
        # update the IP when doing its initial burst
        sleep(3600)
        get_ip()

threading.Thread(target=probing_thread, name='local-webpage-searcher', daemon=True).start()
threading.Thread(target=update_ip, name='IP-watcher', daemon=True).start()
service_guard = ActionTimeout(30)
long_guard = ActionTimeout(600)
update_guard = ActionTimeout(600)

# ========= API Routes =========

@app.route('/api/localpages')
def api_localpages():
    global localpages
    localpages = local_webpage_prober()
    return jsonify(localpages)

# ========= Root Route =========

@app.route('/')
def landing_page():
    version = get_version()
    webapp_version = get_webapp_version()
    status = get_flightgazer_status()
    hostname = HOSTNAME
    ip_address = CURRENT_IP
    # Pass localpages to the template
    return render_template(
        'landing.html',
        version=version,
        status=status,
        localpages=localpages,
        webapp_version=webapp_version,
        hostname = hostname,
        ip_address = ip_address
    )

# ========= Service Control Routes =========

@app.route('/restart', methods=['POST'])
def restart_flightgazer():
    try:
        status = get_flightgazer_status()
        if status == 'stopped':
            if service_guard.touch():
                subprocess.Popen(
                    ['systemctl', 'start', 'flightgazer'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            else:
                raise PermissionError
        # Restart the systemd service
        else:
            main_logger.info("FlightGazer service restart requested")
            if service_guard.touch():
                subprocess.run(['systemctl', 'restart', 'flightgazer'], check=True, timeout=15)
            else:
                raise PermissionError
        main_logger.info("FlightGazer service restart successful")
        return jsonify({'status': 'success'})
    except PermissionError:
        message = "Service control timeout still active, try again later."
        main_logger.warning(message)
        return jsonify({'status': 'error', 'error': str(message)})
    except Exception as e:
        main_logger.exception("FlightGazer service restart failed.")
        return jsonify({'status': 'error', 'error': str(e)})

@app.route('/service/start', methods=['POST'])
def start_flightgazer_service():
    try:
        status = get_flightgazer_status()
        if status == 'running' or status == 'degraded':
            return jsonify({'status': 'already_running'})
        # Start the service in the background
        main_logger.info("FlightGazer service start requested")
        if service_guard.touch():
            subprocess.Popen(
                ['systemctl', 'start', 'flightgazer'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        else:
            raise PermissionError
        main_logger.info("FlightGazer service start successful")
        return jsonify({'status': 'started'})
    except PermissionError:
        message = "Service control timeout still active, try again later."
        main_logger.warning(message)
        return jsonify({'status': 'error', 'error': str(message)})
    except Exception as e:
        main_logger.exception("FlightGazer service start failed.")
        return jsonify({'status': 'error', 'error': str(e)})

@app.route('/service/stop', methods=['POST'])
def stop_flightgazer_service():
    try:
        status = get_flightgazer_status()
        if status == 'stopped':
            return jsonify({'status': 'already_stopped'})
        main_logger.info("FlightGazer service stop requested")
        if service_guard.touch():
            subprocess.run(['systemctl', 'stop', 'flightgazer'], check=True)
        else:
            raise PermissionError
        main_logger.info("FlightGazer service stop successful")
        return jsonify({'status': 'stopped'})
    except PermissionError:
        message = "Service control timeout still active, try again later."
        main_logger.warning(message)
        return jsonify({'status': 'error', 'error': str(message)})
    except Exception as e:
        main_logger.exception("FlightGazer service stop failed")
        return jsonify({'status': 'error', 'error': str(e)})

@app.route('/api/status')
def service_status():
    status = get_flightgazer_status()
    return jsonify({'status': status})

# ========= Config Modification Routes =========

@app.route('/config')
def config_page():
    try:
        config = load_config()
    except Exception as e:
        main_logger.exception("Failed to load config file!")
        return make_response(
            ('<h1>ERROR: config file is missing or could not be loaded!<br>'
            'You must re-install FlightGazer.</h1><br>' \
            '<h2>Please go back and go to \"Check for Updates\" to do the re-install.</h2><br>'
            f'<h3>Details: {e}</h3>'), 200)
    try:
        predefined_colors, predefined_colors_rgb = get_predefined_colors()
        color_config = get_color_config()
    except Exception as e:
        return make_response(
            ('<h1>ERROR: color config file is missing or could not be loaded!<br>'
            'You must re-install FlightGazer.</h1><br>' \
            '<h2>Please go back and go to \"Check for Updates\" to do the re-install.</h2><br>'
            f'<h3>Details: {e}</h3>'), 200)
    return render_template(
        'config.html',
        config=config,
        predefined_colors=predefined_colors,
        predefined_colors_rgb=predefined_colors_rgb,
        color_config=color_config
    )

@app.route('/update', methods=['POST'])
def update_config():
    data = request.json
    try:
        config = load_config()
    except Exception as e:
        main_logger.exception("Failed to load config file!")
        return jsonify({'error': f'{e}'})
    # List of all boolean (checkbox) settings
    bool_keys = [
        'JOURNEY_PLUS', 'ENHANCED_READOUT', 'ENHANCED_READOUT_AS_FALLBACK', 'SHOW_EVEN_MORE_INFO',
        'CLOCK_24HR', 'ALTERNATIVE_FONT', 'DISPLAY_SWITCH_PROGRESS_BAR',
        'ENABLE_TWO_BRIGHTNESS', 'USE_SUNRISE_SUNSET',
        'PREFER_LOCAL', 'HAT_PWM_ENABLED',
        'FASTER_REFRESH', 'NO_GROUND_TRACKING', # 'FLYBY_STATS_ENABLED', 'WRITE_STATE'
        'CLOCK_CENTER_ROW_CYCLE', 'PREFER_ICAO_CODES', 'EXTENDED_DETAILS', 'DISABLE_ACTIVE_BRIGHTNESS_AT_NIGHT'
    ]
    # List of all numeric settings that may be missing
    numeric_keys = [
        'BRIGHTNESS_2',
        'LED_PWM_BITS'
    ]
    # Update config with posted data
    for key, value in data.items():
        match key:
            case 'API_SCHEDULE':
                config['API_SCHEDULE']['ENABLED'] = value['ENABLED']
                for day in ['SUNDAY','MONDAY','TUESDAY','WEDNESDAY','THURSDAY','FRIDAY','SATURDAY']:
                    config['API_SCHEDULE'][day]['0-11'] = value[day]['0-11']
                    config['API_SCHEDULE'][day]['12-23'] = value[day]['12-23']
            case 'BRIGHTNESS_SWITCH_TIME':
                config['BRIGHTNESS_SWITCH_TIME']['Sunrise'] = value['Sunrise']
                config['BRIGHTNESS_SWITCH_TIME']['Sunset'] = value['Sunset']
            case 'CLOCK_CENTER_ROW':
                config['CLOCK_CENTER_ROW']['ROW1'] = value['ROW1']
                config['CLOCK_CENTER_ROW']['ROW2'] = value['ROW2']
            case 'colors':
                # Do not add colors to config dict
                pass
            case _:
                config[key] = value
    # skip entries that don't have an associated key
    for key in bool_keys:
        if key not in data:
            continue
    for key in numeric_keys:
        if key not in data:
            continue
    # Remove any lingering 'colors' key from config dict if present
    if 'colors' in config:
        del config['colors']
    try:
        save_config(config)
        if 'colors' in data:
            save_color_config(data['colors'])
    except Exception as e:
        main_logger.exception("Failed to save to config file!")
        return jsonify({'error': f'{e}'})
    return jsonify({'status': 'success'})

# ========= Details and Log Routes =========

@app.route('/details')
def details_page():
    return render_template('details.html')

current_state_json_cache: str | dict | None = None
@app.route('/details/live')
def details_live():
    global current_state_json_cache
    json_path = CURRENT_STATE_JSON_PATH
    if not os.path.exists(json_path) and os.name == 'nt': # for debug
        json_path = os.path.join(os.path.dirname(__file__), '..', 'current_state.json')
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            f.seek(0)
            current_json = f.read()
        current_state_json_cache = current_json
        return jsonify(data)
    except FileNotFoundError as e:
        response_json = {
            'Info':
            {
                'status': 'Current state file could not be found.',
                'what_to_do': (
                    'FlightGazer may not be running or '
                    'the setting to write a state file is disabled. '
                    'Please check the other logs.')
            },
             'More_Info':
            {
                'error_description': f'{e.__class__.__name__}: {e}',
                'timestamp': append_date_now()
            }
        }
        current_state_json_cache = response_json
        return jsonify(response_json)
    except json.decoder.JSONDecodeError as e:
        response_json = {
            'Info':
            {
                'status': f'The state file is currently blank or could not be decoded.',
                'what_to_do': (
                    'Please refresh again in a few moments. '
                    'If this message does not go away, please check the other logs.')
            },
            'More_Info':
            {
                'error_description': f'{e.__class__.__name__}: {e}',
                'timestamp': append_date_now()
            }
        }
        current_state_json_cache = response_json
        return jsonify(response_json)
    except Exception as e:
        response_json = {
            'Error':
             {
                'status': f'Could not load state file. {e.__class__.__name__}:{e}',
                'timestamp': append_date_now()
            }
        }
        current_state_json_cache = response_json
        return jsonify(response_json)

@app.route('/data/current_state.json')
def show_json():
    """ Special endpoint if the end user wants to poll the current state of
    FlightGazer via the webapp (basically the same as `download_json()` but doesn't
    prompt for download) """
    json_path = CURRENT_STATE_JSON_PATH
    if not os.path.exists(json_path): # for debug
        json_path = os.path.join(os.path.dirname(__file__), '..', 'current_state.json')
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return f'JSON file not found: {e}', 404

@app.route('/details/download_json')
def download_json():
    json_path = CURRENT_STATE_JSON_PATH
    if not os.path.exists(json_path): # for debug
        json_path = os.path.join(os.path.dirname(__file__), '..', 'current_state.json')
    if not current_state_json_cache:
        details_live()
    try:
        buffer = io.BytesIO()
        main_logger.info("FlightGazer current state download requested")
        if isinstance(current_state_json_cache, dict): # handle case when downloading the error codes
            cache_reencode = json.dumps(current_state_json_cache)
            buffer.write(cache_reencode.encode('utf-8'))
            buffer.seek(0)
        else:
            buffer.write(current_state_json_cache.encode('utf-8'))
            buffer.seek(0)
        return send_file(
            buffer,
            mimetype='application/json',
            as_attachment=True,
            download_name=f'FlightGazer-current_state_[{append_date_now()}].json'
        )
    except Exception: # just fall back on downloading the file directly
        try:
            return send_file(
                json_path,
                mimetype='application/json',
                as_attachment=True,
                download_name=f'FlightGazer-current_state_[{append_date_now()}].json'
            )
        except Exception as e:
            return f'JSON file not found: {e}', 404

@app.route('/details/log')
def details_log():
    if not os.path.exists(LOG_PATH):
        raise FileNotFoundError
    try:
        return send_file(LOG_PATH, mimetype='text/plain')
    except Exception as e:
        return f'Log file not found: {e}', 404

@app.route('/details/download_log')
def download_log():
    if not os.path.exists(LOG_PATH):
        raise FileNotFoundError
    try:
        main_logger.info("FlightGazer log download requested")
        return send_file(
            LOG_PATH,
            mimetype='text/plain',
            as_attachment=True,
            download_name=f'FlightGazer-log_[{append_date_now()}].log'
        )
    except Exception as e:
        return f'Log file not found: {e}', 404

@app.route('/details/log_html')
def details_log_html():
    if not os.path.exists(LOG_PATH):
        raise FileNotFoundError
    try:
        with open(LOG_PATH, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        html_lines = []
        for line in lines:
            # Highlight timestamp
            line = re.sub(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)', r'<span style="color:#ffd700;">\1</span>', line)
            # Highlight log levels
            line = re.sub(r'\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b', lambda m: f'<span style="color:{ {"DEBUG":"#8cf","INFO":"#6f6","WARNING":"#ff0","ERROR":"#f66","CRITICAL":"#f00"}[m.group(1)]};font-weight:bold;">{m.group(1)}</span>', line)
            # Highlight thread names
            line = re.sub(r'\| ([A-Za-z0-9\-]+) \|', r'| <span style="color:#b2dfdb;">\1</span> |', line)
            html_lines.append(line)
        html = '<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{background:#181818;color:#fff;font-family:monospace;font-size:1em;margin:0;padding:12px;} .logline{white-space:pre;}</style></head><body>'
        html += '\n'.join(f'<div class="logline">{l}</div>' for l in html_lines)
        html += '</body></html>'
        return html
    except Exception as e:
        return f'<span style="color:#f66;font-family:monospace">Log file not found: {e.__class__.__name__}: {e}</span>', 404

@app.route('/details/migrate_log')
def details_migrate_log():
    if not os.path.exists(MIGRATE_LOG_PATH):
        return '<div style="color:#f66;font-family:monospace;padding:12px;">Migration history log not found or an update has not been done yet.</div>', 404
    try:
        with open(MIGRATE_LOG_PATH, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        line_count = content.count('\n') + 1
        header_line = ''
        if line_count > 200:
            log_content_split = content.splitlines()
            content = '\n'.join(log_content_split[-200:])
            header_line = (
                f'<div class="logline"><i>Showing the latest 200 lines (out of {line_count})</i>.<br>'
                'Download the file for complete data.<br><br></div>'
            )
        html = '<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{background:#181818;color:#fff;font-family:monospace;font-size:1em;margin:0;padding:12px;} .logline{white-space:pre;}</style></head><body>'
        if header_line:
            html += header_line
        safe_content = content.replace('<', '&lt;').replace('>', '&gt;')
        html += f'<div class="logline">{safe_content}</div>'
        html += '</body></html>'
        return html
    except Exception as e:
        return f'<span style="color:#f66;font-family:monospace">Log file not found.<br>{e.__class__.__name__}: {e}</span>', 404

@app.route('/details/download_migrate_log')
def download_migrate_log():
    if not os.path.exists(MIGRATE_LOG_PATH):
        return 'Migration history log not found or an update has not been done yet.', 404
    try:
        main_logger.info("Migration log download requested")
        return send_file(
            MIGRATE_LOG_PATH,
            mimetype='text/plain',
            as_attachment=True,
            download_name=f'FlightGazer-settings_migrate_[{append_date_now()}].log'
        )
    except Exception as e:
        return f'Log file not found: {e}', 404

init_log_cache = None
@app.route('/details/init_log')
def details_init_log():
    global init_log_cache
    if not is_posix:
        init_log_cache = None
        return f'<span style="color:#f66;font-family:monospace;">Initialization log is unavailable on this system.</span>', 404
    try:
        # Fetch logs for FlightGazer-init.sh via journalctl (unit: flightgazer.service or fallback to grep script name)
        result = subprocess.run([
            'journalctl', '-u', 'flightgazer.service', '--no-pager', '--output=short-precise'
        ], capture_output=True, text=True)
        if result.returncode != 0 or not result.stdout.strip():
            # Fallback: grep for FlightGazer-init.sh in all logs
            result = subprocess.run([
                'journalctl', '--no-pager', '--output=short-precise', '|', 'grep', 'FlightGazer-init.sh'
            ], capture_output=True, text=True, shell=True)
        log_content = result.stdout if result.returncode == 0 else '(No init log found)'
        init_log_cache = log_content
        line_count = log_content.count('\n') + 1
        header_line = ''
        if line_count > 500:
            log_content_split = log_content.splitlines()
            log_content = '\n'.join(log_content_split[-500:])
            header_line = (
                f'<div class="logline"><i>Showing the latest 500 lines (out of {line_count})</i>.<br>'
                'Download the file for complete data.<br><br></div>'
            )

        html = '<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{background:#181818;color:#fff;font-family:monospace;font-size:1em;margin:0;padding:12px;} .logline{white-space:pre;}</style></head><body>'
        if header_line:
            html += header_line
        safe_content = log_content.replace('<', '&lt;').replace('>', '&gt;')
        html += f'<div class="logline">{safe_content}</div>'
        html += '</body></html>'
        return html
    except Exception as e:
        init_log_cache = None
        return f'<span style="color:#f66;font-family:monospace;">Initialization log not found or unavailable on this system.<br>{e.__class__.__name__}: {e}</span>', 404

@app.route('/details/download_init_log')
def download_init_log():
    details_init_log()
    if not init_log_cache or init_log_cache == '(No init log found)':
        return 'Initialization log is not available:', 404
    else:
        try:
            buffer = io.BytesIO()
            buffer.write(init_log_cache.encode('utf-8'))
            buffer.seek(0)
            main_logger.info("FlightGazer init log download requested")
            return send_file(
                buffer,
                mimetype='text/plain',
                as_attachment=True,
                download_name=f'FlightGazer-initialization_[{append_date_now()}].log'
            )
        except Exception as e:
            return f'Initialization log is not available: {e}', 404

@app.route('/details/download_flybys_csv')
def download_flybys_csv():
    if not os.path.exists(FLYBY_STATS_PATH):
        return 'flybys.csv not found', 404
    try:
        main_logger.info("Flybys stats file download requested")
        return send_file(
            FLYBY_STATS_PATH,
            mimetype='text/csv',
            as_attachment=True,
            download_name='flybys.csv'
        )
    except Exception as e:
        return f'CSV file not found: {e}', 404

@app.route('/details/flybys_csv')
def details_flybys_csv():
    if not os.path.exists(FLYBY_STATS_PATH):
        return '<div style="color:#f66;font-family:monospace;padding:12px;">flybys.csv not found.</div>', 404
    try:
        with open(FLYBY_STATS_PATH, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        html_ = []
        html_.append(
            '<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{background:#181818;color:#fff;font-family:monospace;font-size:1em;margin:0;padding:12px;} '
            'table{border-collapse:collapse;width:100%;} th,td{border:1px solid #444;padding:4px 8px;} '
            'th{position:sticky;top:0;z-index:1;background:#222;color:#ffd700;} '
            'tr:nth-child(even){background:#222;} tr:nth-child(odd){background:#181818;} '
            'caption{color:#ffd700;font-weight:bold;margin-bottom:8px;}</style></head><body>'
        )
        if lines:
            html_.append('<table>')
            csv_len = len(lines)
            if csv_len > 761: # header + 760 entries (~1 month + a small buffer)
                lines_ = lines[-760:]
                lines_.insert(0, lines[0])
                lines = lines_
                html_.append(f'<i>Showing the latest 760 lines (out of {csv_len}).</i><br>Download the file for complete data.<br><br>')
            for i, line in enumerate(lines):
                cells = [c.strip() for c in line.strip().split(',')]
                if i == 0:
                    cols = ''.join(f'<th>{c}</th>' for c in cells)
                    html_.append(f'<tr>{cols}</tr>')
                else:
                    cols = ''.join(f'<td>{c}</td>' for c in cells)
                    html_.append(f'<tr>{cols}</tr>')
            html_.append('</table>')
        else:
            html_.append('<div style="color:#aaa">(No data in flybys.csv)</div>')
        html_.append('</body></html>')
        return ''.join(html_)
    except Exception as e:
        return f'<span style="color:#f66;font-family:monospace">File could not be loaded.<br>{e.__class__.__name__}: {e}</span>', 404

@app.route('/details/log-reference')
def detail_reference():
    return render_template('details-reference.html')

# ========= Startup Control Routes =========

@app.route('/startup')
def startup_options():
    return render_template('startup.html')

STARTUP_OPTIONS = [
    {'name': 'No Display mode', 'flag': '-d'},
    {'name': 'Emulate display (use RGBMatrixEmulator)', 'flag': '-e'},
    {'name': 'No Filter mode', 'flag': '-f'},
    {'name': 'Verbose/debug mode', 'flag': '-v'},
]

@app.route('/startup/options')
def get_startup_options():
    if not os.path.exists(SERVICE_PATH):
        return jsonify({'error': 'Service file does not exist. FlightGazer might not be installed.'}), 500
    # Read current flags from ExecStart in service file
    try:
        with open(SERVICE_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        exec_line = next((l for l in lines if l.strip().startswith('ExecStart=')), '')
        flags = []
        for opt in STARTUP_OPTIONS:
            if f' {opt["flag"]}' in exec_line:
                flags.append(opt['flag'])
        return jsonify({'flags': flags, 'options': STARTUP_OPTIONS})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/startup/set', methods=['POST'])
def set_startup_options():
    if not os.path.exists(SERVICE_PATH):
        return jsonify({'error': 'Service file does not exist. FlightGazer might not be installed.'}), 500

    main_logger.info("Service file change requested")
    data = request.json
    selected_flags = data.get('flags', [])
    # Validate that all flags are in the allowed options list
    allowed_flags = {opt['flag'] for opt in STARTUP_OPTIONS}
    allowed_flags.add('-t')  # always add this

    if not all(flag in allowed_flags for flag in selected_flags):
        main_logger.error("Invalid startup flags passed to service config")
        return jsonify({'error': 'Invalid startup flags detected'}), 400

    try:
        if long_guard.touch():
            pass
        else:
            raise PermissionError

        with open(SERVICE_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        # validate service file contains expected format
        if not any(line.strip().startswith('ExecStart=') for line in lines):
            main_logger.error("Invalid service file format")
            return jsonify({'error': 'Invalid service file format'}), 500

        for i, line in enumerate(lines):
            if line.strip().startswith('ExecStart='):
                # construct command with strict path validation
                if not os.path.isfile(INIT_PATH):
                    main_logger.error("Could not find initialization script")
                    return jsonify({'error': 'Initialization script not found'}), 500

                # build command with validated flags
                new_line = f'ExecStart=bash "{INIT_PATH}" -t'
                for flag in selected_flags:
                    if flag in allowed_flags and flag != '-t':
                        new_line += f' {flag}'
                new_line += '\n'
                lines[i] = new_line

        # validate final command doesn't contain sus characters
        if any(char in new_line for char in ';&|$()`'):
            main_logger.error("Invalid characters found in service file commands")
            return jsonify({'error': 'Invalid characters in command'}), 400

        with open(SERVICE_PATH, 'w', encoding='utf-8') as f:
            f.writelines(lines)

        # run systemctl daemon-reload with subprocess security
        subprocess.run(['systemctl', 'daemon-reload'],
                     check=True,
                     shell=False,
                     timeout=15)

        main_logger.info("Service file successfully updated")
        return jsonify({'status': 'success'})

    except PermissionError:
        message = "Lockout for updating startup options is still active, try again later."
        main_logger.warning(f"{message}")
        return jsonify({'error': str(message)}), 500
    except subprocess.TimeoutExpired:
        main_logger.error("Service file reload timed out")
        return jsonify({'error': 'Command timed out'}), 500
    except Exception as e:
        main_logger.exception("Uncaught error in service file update")
        return jsonify({'error': str(e)}), 500

# ========= Updates Handling Routes and Functions =========

@app.route('/updates')
def updates_page():
    return render_template('updates.html')

update_process = None
update_output_queue = queue.Queue()
update_input_queue = queue.Queue()
update_lock = threading.Lock()
update_running = False

class UpdateRunner(threading.Thread):
    """ Runs the update script and gathers output to be sent to the webpage. """
    def __init__(self, script_args):
        super().__init__()
        self.script_args = script_args # list: [script_path, ...flags]
        self.proc = None
        self.daemon = True
        self.prompted = False
        self.prompted_count = 0
    def run(self):
        global update_running
        update_running = True
        # Always run with bash, pass script path and any flags
        self.proc = subprocess.Popen(
            ['bash'] + self.script_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        output_history = []
        while True:
            line = self.proc.stdout.readline()
            if line == '' and self.proc.poll() is not None:
                break # subprocess has completed
            if line:
                output_history.append(remove_ansi_escape(line))
                update_output_queue.put(''.join(output_history))
                if 'you like' in line:
                    if self.prompted_count == 0:
                        self.prompted = True
                        self.prompted_count += 1
                        self.proc.stdin.write('y\n')
                        self.proc.stdin.flush()
                        self.prompted = False
                        continue
                    else: # we're stuck in a prompt loop; break out
                        self.proc.terminate()
                        update_running = False
                        break
                if '>>> Update complete' in line: # we're done, don't wait for subprocesses
                    self.proc.terminate()
                    update_running = False
                    break
        main_logger.info("FlightGazer update process has finished")
        # Drain remaining output
        for line in self.proc.stdout:
            output_history.append(line)
            update_output_queue.put(''.join(output_history))
        update_running = False

@app.route('/updates/start', methods=['POST'])
def start_update():
    global update_process, update_output_queue, update_input_queue, update_running
    # Get reset_config flag from frontend
    reset_config = False
    if request.is_json:
        data = request.get_json()
        reset_config = data.get('reset_config', False)
    main_logger.info("Starting FlightGazer update process")
    if update_guard.touch():
        pass
    else:
        message = "Update timeout still active. Try again at a later time."
        main_logger.warning(message)
        return jsonify({'status': str(message)})
    with update_lock:
        if update_running:
            return jsonify({'status':'already_running'})
        update_output_queue = queue.Queue()
        update_input_queue = queue.Queue()
        script_args = [UPDATE_PATH]
        if reset_config:
            script_args.append('-r')
        update_process = UpdateRunner(script_args)
        update_process.start()
    return jsonify({'status':'started'})

@app.route('/updates/output')
def get_update_output():
    lines = []
    while not update_output_queue.empty():
        lines.append(update_output_queue.get())
    # Only return the last output (full history)
    last_output = lines[-1] if lines else ''
    return jsonify({'lines': [last_output], 'running': update_running})

@app.route('/updates/input', methods=['POST'])
def send_update_input():
    user_input = request.json.get('input')
    update_input_queue.put(user_input)
    return jsonify({'status':'sent'})

# def get_local_changelog():
#     changelog_path = os.path.join(os.path.dirname(__file__), 'Changelog.txt')
#     try:
#         with open(changelog_path, 'r', encoding='utf-8') as f:
#             return f.read()
#     except Exception:
#         return ''

@app.route('/updates/check', methods=['POST'])
def check_for_updates():
    try:
        update_fetcher()
    except requests.exceptions.RequestException as e:
        main_logger.error(f"Update checker failed to fetch remote files. {e}")
        return jsonify({'error': f'Failed to fetch remote files due to a connection issue. Check the network. Details ---> {e}'}), 500
    except Exception as e:
        main_logger.exception("Could not fetch remote files.")
        return jsonify({'error': f'Failed to fetch remote files due to an internal error. Details ---> {e.__class__.__name__}: {e}'}), 500
    local_ver = get_version()
    # Truncate changelog to only show entries newer than local version
    lines = remote_changelog.splitlines()
    truncated = []
    for line in lines:
        if line.strip().startswith(f'v.{local_ver} '):
            break
        truncated.append(line)
    # If found, only show lines above current version
    changelog_to_show = '\n'.join(truncated).strip()
    return jsonify({
        'local_version': local_ver,
        'latest_version': remote_ver,
        'changelog': changelog_to_show
    })

@app.route("/data/latest_changelog_cached.txt")
def show_latest_changelog():
    if not remote_changelog:
        try:
            update_fetcher()
            response = make_response(remote_changelog, 200)
            response.mimetype = "text/plain"
            return response
        except Exception:
            return 'Could not fetch latest changelog.', 404
    else:
        try:
            response = make_response(remote_changelog, 200)
            response.mimetype = "text/plain"
            return response
        except Exception as e:
            return f'Could not fetch latest changelog. ({e})', 500

# ========= Help/Reference html =========
@app.route('/reference')
def reference_guide():
    adsb_info = ''
    adsb_logs = ''
    valid_keys = [
        'System Configuration',
        'SDR Disconnected'
    ]
    if RUNNING_ADSBIM:
        key_view = list(localpages.keys())
        for lookup_key in valid_keys:
            found = False
            for available_key in key_view:
                if lookup_key in available_key:
                    if isinstance((adsb_root_ := localpages[available_key]), str):
                        adsb_info = adsb_root_ + '/info'
                        adsb_logs = adsb_root_ + '/logs'
                        found = True
                        break
            if found:
                break

    device_desc = f'{HOSTNAME}, local IP address: {CURRENT_IP}'
    return render_template(
        'reference.html',
        is_adsbim = RUNNING_ADSBIM,
        adsb_info = adsb_info,
        adsb_logs = adsb_logs,
        device_name = device_desc
    )

# ========= Misc =========
if os.path.exists(VERSION_PATH):
    main_logger.info(f"Found FlightGazer ({get_version()})")

# ========= Debugging =========
if __name__ == '__main__':
    if os.name == 'nt':
        app.run(debug=True, port=9898)