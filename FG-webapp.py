""" FlightGazer web interface. Handles editing the config file,
reading the logs, checking for updates, and modifying startup options.
This won't run if FlightGazer isn't installed on the system as it depends on
FlightGazer's virtual environment. (should be handled via the install script).
Disclosures: most of this was initially vibe-coded, but does contain manual edits to make sure
it actually works (mostly) and that the actual web pages aren't too janky. """

from flask import Flask, render_template, request, jsonify, send_file, send_from_directory
from ruamel.yaml import YAML
import os
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

VERSION = "v.0.12.3 --- 2025-11-06"

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

webapp_session = requests.session()

yaml = YAML()
yaml.preserve_quotes = True

app = Flask(__name__)
app.json.sort_keys = False

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

# ========= Helper Functions =========

def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        data = yaml.load(f)
    return data

def save_config(data):
    """ Save config and preserve owner/group """
    orig_stat = None
    if os.path.exists(CONFIG_PATH):
        orig_stat = os.stat(CONFIG_PATH)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(data, f)
    if orig_stat and os.name != 'nt':
        os.chown(CONFIG_PATH, orig_stat.st_uid, orig_stat.st_gid)

def get_predefined_colors():
    """  Parse colors.py for color names and RGB values """
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
    """ Parse colors.py for current color assignments """
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
    """  Update colors.py assignments for color_vars and preserve owner/group """
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
    if orig_stat and os.name != 'nt':
        os.chown(COLORS_PATH, orig_stat.st_uid, orig_stat.st_gid)

def get_version():
    try:
        with open(VERSION_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return 'Unknown'

def get_webapp_version():
    try:
        with open(WEBAPP_VERSION_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return 'Unknown'

def get_ip() -> None:
    """ Gets us our local IP. Pulled exactly as is from the main FlightGazer script.
    Modifies the global `CURRENT_IP` """
    global CURRENT_IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0)
    try:
        s.connect(('10.254.254.254', 1)) # doesn't even need to connect
        IP = s.getsockname()[0]
    except Exception:
        IP = ""
    finally:
        s.close()
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
    - Port 5173
        - Skystats
    - Port 8088
        - Planefence

    Returns a dict, with the key-value pairs being the link name and URL.
    """
    global RUNNING_ADSBIM
    pages = {}
    def webpage_title(input: str) -> str | None:
        """ Get the title of a webpage given `input` as a string. """
        try:
            response = webapp_session.get(input, timeout=0.5, allow_redirects=True)
            # get the webpage title; https://stackoverflow.com/a/47236359
            resp_body = response.text
            title = re.search('(?<=<title>).+?(?=</title>)', resp_body, re.DOTALL).group().strip()
            return title
        except Exception:
            return None
    def match_title(input: None | str, title: str) -> bool:
        if input and title in input:
            return True
        return False
    # find adsb.im
    adsbim = f"http://{CURRENT_IP}"
    local_page = webpage_title(adsbim)
    if local_page:
        if "Feeder Homepage" in local_page:
            pages.update({"System Configuration & Management, Maps, and Stats": adsbim})
            RUNNING_ADSBIM = True
        elif "PiAware" in local_page: # FlightAware's PiAware is running here
            pages.update({"FlightAware PiAware page": adsbim})
        # else: # something else is running here, link it anyway
        #     pages.update({f"{local_page}"[:100]: adsbim})
    else:
        adsbim = f"http://{CURRENT_IP}:1099"
        local_page = webpage_title(adsbim)
        if match_title(local_page, "Feeder Homepage"):
            pages.update({"System Configuration & Management, Maps, and Stats": adsbim})
            RUNNING_ADSBIM = True
    # try to find the display emulator
    display_emulator = [f"http://{CURRENT_IP}:8888", "http://localhost:8888"]
    for url in display_emulator:
        rgbemulatorpage = webpage_title(url)
        if rgbemulatorpage and any(map(rgbemulatorpage.__contains__, ["FlightGazer", "RGBME"])):
            pages.update({"RGBMatrixEmulator": url})
            break
    # find the rest of our stuff
    tar1090location = [f"http://{CURRENT_IP}/tar1090", f"http://{CURRENT_IP}:8080"]
    for url in tar1090location:
        tar1090page = webpage_title(url)
        if match_title(tar1090page, "tar1090"):
            pages.update({"tar1090 Tracking Interface": url})
            break

    graphs1090location = [f"http://{CURRENT_IP}/graphs1090", f"http://{CURRENT_IP}:8542"]
    for url in graphs1090location:
        graphs1090page = webpage_title(url)
        if match_title(graphs1090page, "graphs1090"):
            pages.update({"graphs1090 Statistics Interface": url})
            break

    skystatslocation = f"http://{CURRENT_IP}:5173"
    skystatspage = webpage_title(skystatslocation)
    if match_title(skystatspage, "Skystats"):
        pages.update({"Skystats": skystatslocation})
    planefencelocation = f"http://{CURRENT_IP}:8088"
    planefencepage = webpage_title(planefencelocation)
    if match_title(planefencepage, "Planefence"):
        pages.update({"Planefence": planefencelocation})
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

def flightgazer_bad_state():
    """ Check if FlightGazer is running in a bad state or has failed """
    return is_posix and os.path.exists(BAD_STATE_SEMAPHORE)

def get_flightgazer_status():
    """ Poll the service status """
    global current_flightgazer_pid, current_flightgazer_process, manually_running
    try:
        # if the previous poll showed that no existing PID for FlightGazer is running, run through this
        if current_flightgazer_process is None or not current_flightgazer_process.is_running():
            result = subprocess.run(['systemctl', 'is-active', 'flightgazer'], capture_output=True, text=True)
            result2 = subprocess.run(['systemctl', 'is-failed', 'flightgazer'], capture_output=True, text=True)
            if result2.returncode == 1: # no failures
                if ((result.returncode == 0 and result.stdout.strip() == 'active')
                    or result.stdout.strip() == 'activating'):
                    # try to find the PID of the running instance
                    if current_flightgazer_pid is None:
                        current_flightgazer_pid = match_commandline('FlightGazer.py', 'python')
                        if current_flightgazer_pid is not None:
                            # The main process of FlightGazer is running (not in the systemd startup phase)
                            current_flightgazer_process = psutil.Process(pid=current_flightgazer_pid)
                            manually_running = False
                    return 'running'
                elif result.returncode == 3 or result.stdout.strip() == 'inactive':
                    current_flightgazer_pid = match_commandline('FlightGazer.py', 'python')
                    if current_flightgazer_pid is not None:
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

# ========= Initialization Stuff =========
get_ip()
def probing_thread() -> None:
    """ Probes available webpages: does an initial burst every
    30 seconds for 5 minutes at startup (to wait for the other pages to start),
    then once every 10 minutes for the next hour,
    then updates once a day thereafter.
    Modifies the `localpages` global. """
    global localpages
    run_count = 0
    while True:
        localpages = local_webpage_prober()
        run_count += 1
        if run_count <= 10:
            sleep(30)
            get_ip()
        elif 11 <= run_count < 17:
            sleep(600)
            get_ip()
        else:
            sleep(86400)

def update_ip() -> None:
    """ Check if the IP changes (every hour) """
    while True:
        # note we sleep first to let the probing thread
        # update the IP when doing its initial burst
        sleep(3600)
        get_ip()

threading.Thread(target=probing_thread, name='local-webpage-searcher', daemon=True).start()
threading.Thread(target=update_ip, name='IP-watcher', daemon=True).start()

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
        # Restart the systemd service
        subprocess.run(['systemctl', 'restart', 'flightgazer'], check=True)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)})

@app.route('/service/start', methods=['POST'])
def start_flightgazer_service():
    import subprocess
    try:
        status = get_flightgazer_status()
        if status == 'running':
            return jsonify({'status': 'already_running'})
        # Start the service in the background
        subprocess.Popen(['systemctl', 'start', 'flightgazer'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return jsonify({'status': 'started'})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)})

@app.route('/service/stop', methods=['POST'])
def stop_flightgazer_service():
    import subprocess
    try:
        subprocess.run(['systemctl', 'stop', 'flightgazer'], check=True)
        return jsonify({'status': 'stopped'})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)})

@app.route('/service/status')
def service_status():
    status = get_flightgazer_status()
    return jsonify({'status': status})

# ========= Config Modification Routes =========

@app.route('/config')
def config_page():
    config = load_config()
    predefined_colors, predefined_colors_rgb = get_predefined_colors()
    color_config = get_color_config()
    return render_template('config.html', config=config, predefined_colors=predefined_colors, predefined_colors_rgb=predefined_colors_rgb, color_config=color_config)

@app.route('/update', methods=['POST'])
def update_config():
    data = request.json
    config = load_config()
    # List of all boolean (checkbox) settings
    bool_keys = [
        'JOURNEY_PLUS', 'ENHANCED_READOUT', 'ENHANCED_READOUT_AS_FALLBACK', 'SHOW_EVEN_MORE_INFO',
        'CLOCK_24HR', 'ALTERNATIVE_FONT', 'DISPLAY_SWITCH_PROGRESS_BAR',
        'ENABLE_TWO_BRIGHTNESS', 'USE_SUNRISE_SUNSET',
        'PREFER_LOCAL', 'HAT_PWM_ENABLED',
        'FASTER_REFRESH', # 'FLYBY_STATS_ENABLED', 'WRITE_STATE'
    ]
    # List of all numeric settings that may be missing
    numeric_keys = [
        'BRIGHTNESS_2',
        'LED_PWM_BITS'
    ]
    # Update config with posted data
    for key, value in data.items():
        if key == 'API_SCHEDULE':
            config['API_SCHEDULE']['ENABLED'] = value['ENABLED']
            for day in ['SUNDAY','MONDAY','TUESDAY','WEDNESDAY','THURSDAY','FRIDAY','SATURDAY']:
                config['API_SCHEDULE'][day]['0-11'] = value[day]['0-11']
                config['API_SCHEDULE'][day]['12-23'] = value[day]['12-23']
        elif key == 'BRIGHTNESS_SWITCH_TIME':
            config['BRIGHTNESS_SWITCH_TIME']['Sunrise'] = value['Sunrise']
            config['BRIGHTNESS_SWITCH_TIME']['Sunset'] = value['Sunset']
        elif key == 'CLOCK_CENTER_ROW':
            config['CLOCK_CENTER_ROW']['ROW1'] = value['ROW1']
            config['CLOCK_CENTER_ROW']['ROW2'] = value['ROW2']
        elif key == 'colors':
            # Do not add colors to config dict
            pass
        else:
            config[key] = value
    # Ensure all checkboxes are set (False if missing)
    for key in bool_keys:
        if key not in data:
            config[key] = False
    # Ensure all numeric fields are set (0 if missing)
    for key in numeric_keys:
        if key not in data:
            config[key] = 0
    # Remove any lingering 'colors' key from config dict if present
    if 'colors' in config:
        del config['colors']
    save_config(config)
    if 'colors' in data:
        save_color_config(data['colors'])
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
                'what_to_do': 'FlightGazer may not be running or the setting to write a state file is disabled. Please check the other logs.'
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
                'what_to_do': 'Please refresh again in a few moments. If this message does not go away, please check the other logs.',
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
        if isinstance(current_state_json_cache, dict): # handle case when downloading the error codes
            cache_reencode = json.dumps(current_state_json_cache)
            buffer.write(cache_reencode.encode('utf-8'))
            buffer.seek(0)
        else:
            buffer.write(current_state_json_cache.encode('utf-8'))
            buffer.seek(0)
        return send_file(buffer, mimetype='application/json', as_attachment=True, download_name=f'FlightGazer-current_state_[{append_date_now()}].json')
    except Exception: # just fall back on downloading the file directly
        try:
            return send_file(json_path, mimetype='application/json', as_attachment=True, download_name=f'FlightGazer-current_state_[{append_date_now()}].json')
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
        return send_file(LOG_PATH, mimetype='text/plain', as_attachment=True, download_name=f'FlightGazer-log_[{append_date_now()}].log')
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
        html = '<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{background:#181818;color:#fff;font-family:monospace;font-size:1em;margin:0;padding:12px;} .logline{white-space:pre;}</style></head><body>'
        # Use .replace with double quotes to avoid confusion with f-string braces
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
        return send_file(MIGRATE_LOG_PATH, mimetype='text/plain', as_attachment=True, download_name=f'FlightGazer-settings_migrate_[{append_date_now()}].log')
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
        html = '<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{background:#181818;color:#fff;font-family:monospace;font-size:1em;margin:0;padding:12px;} .logline{white-space:pre;}</style></head><body>'
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
            return send_file(buffer, mimetype='text/plain', as_attachment=True, download_name=f'FlightGazer-initialization_[{append_date_now()}].log')
        except Exception as e:
            return f'Initialization log is not available: {e}', 404

@app.route('/details/download_flybys_csv')
def download_flybys_csv():
    if not os.path.exists(FLYBY_STATS_PATH):
        return 'flybys.csv not found', 404
    try:
        return send_file(FLYBY_STATS_PATH, mimetype='text/csv', as_attachment=True, download_name='flybys.csv')
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
            if csv_len > 1001: # header + 1000 entries
                lines_ = lines[-1000:]
                lines_.insert(0, lines[0])
                lines = lines_
                html_.append(f'<i>Showing the latest 1000 lines (out of {csv_len}).</i><br>Download the file for complete data.<br><br>')
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
    data = request.json
    selected_flags = data.get('flags', [])
    try:
        with open(SERVICE_PATH, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.strip().startswith('ExecStart='):
                # Always include -t, then add selected flags
                new_line = f'ExecStart=bash "{INIT_PATH}" -t'
                for flag in selected_flags:
                    if flag != '-t':
                        new_line += f' {flag}'
                new_line += '\n'
                lines[i] = new_line
        with open(SERVICE_PATH, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        # Run systemctl daemon-reload
        import subprocess
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        return jsonify({'status': 'success'})
    except Exception as e:
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

remote_ver = None
remote_changelog = None
@app.route('/updates/check', methods=['POST'])
def check_for_updates():
    global remote_ver, remote_changelog
    # Fetch remote version and changelog
    try:
        if remote_ver is None:
            remote_ver = requests.get('https://raw.githubusercontent.com/WeegeeNumbuh1/FlightGazer/main/version', timeout=10).text.strip()
            remote_changelog = requests.get('https://raw.githubusercontent.com/WeegeeNumbuh1/FlightGazer/main/Changelog.txt', timeout=10).text
        else:
            remote_ver_ = requests.get('https://raw.githubusercontent.com/WeegeeNumbuh1/FlightGazer/main/version', timeout=10).text.strip()
            if remote_ver_ != remote_ver:
                remote_ver = remote_ver_
                remote_changelog = requests.get('https://raw.githubusercontent.com/WeegeeNumbuh1/FlightGazer/main/Changelog.txt', timeout=10).text
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Failed to fetch remote files due to a connection issue. Check the network. Details ---> {e}'}), 500
    except Exception as e:
        return jsonify({'error': f'Failed to fetch remote files due to an internal error. Details ---> {e.__class__.__name__}: {e}'}), 500
    local_ver = get_version()
    # Truncate changelog to only show entries newer than local version
    lines = remote_changelog.splitlines()
    truncated = []
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(f'v.{local_ver} '):
            found = True
            break
        truncated.append(line)
    # If found, only show lines above current version
    changelog_to_show = '\n'.join(truncated).strip()
    return jsonify({
        'local_version': local_ver,
        'latest_version': remote_ver,
        'changelog': changelog_to_show
    })

# ========= Help/Reference html ==========
@app.route('/reference')
def reference_guide():
    return render_template('reference.html', is_adsbim=RUNNING_ADSBIM)

# ========= Debugging =========
if __name__ == '__main__':
    if os.name == 'nt':
        import importlib.metadata
        print(f"Running Flask version: {importlib.metadata.version('flask')}")
        app.run(debug=True, port=9898)