# Patcher that allows production-releases of adsb.im images/apps
# to proxy the FlightGazer webapp to /flightgazer instead of
# having to type in :9898/flightgazer all the time.
# This script will always try to patch the necessary files
# to a consistent state, and should be run after every
# adsb.im update/rebuild.
# Version: v.1.1.1
# Last adsb.im version tested: v3.0.13
# by: WeegeeNumbuh1

print("********** FlightGazer adsb.im Proxy Patcher **********\n")
import os
import sys
import fileinput
import shutil
from pathlib import Path
import subprocess

ADSBIM_ROOT = Path('/opt/adsb')
MAIN_FILE = Path(ADSBIM_ROOT.absolute(), 'adsb-setup', 'app.py')
DATA_FILE = Path(ADSBIM_ROOT.absolute(), 'adsb-setup', 'utils', 'data.py')
HTML_FILE = Path(ADSBIM_ROOT.absolute(), 'adsb-setup', 'templates', 'base.html')
ADSB_VER = Path(ADSBIM_ROOT.absolute(), 'adsb.im.version')

if not ADSBIM_ROOT.exists():
    print(f"Did not find {ADSBIM_ROOT.absolute()}, nothing to do.")
    sys.exit(1)

integrity = [MAIN_FILE, DATA_FILE, HTML_FILE]
for target in integrity:
    if not target.is_file():
        print(f"Failed to find {target.absolute()}, cannot continue.")
        sys.exit(1)

if os.geteuid() != 0:
    print("This must be run as root.")
    sys.exit(1)

with open(ADSB_VER, "r", encoding="utf-8") as f:
    adsb_version = f.read().strip()
print(f"Found adsb.im version: '{adsb_version}'")

def anchor_finder(file: Path, target_line: str) -> int | None:
    for lineno, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
        if target_line in line:
            return lineno
    else:
        return None

def inserter(file: Path, text: str, lineno: int) -> None:
    if not file or lineno <= 0:
        return
    with fileinput.input(file, inplace=True) as f:
        for line in f:
            if f.filelineno() == lineno:
                print(text)
            print(line, end="")

def main_patcher() -> bool:
    print(f"> Patching {MAIN_FILE.absolute()}...")
    # this will get inserted in the `AdsbIm` class
    # 1 indent (4 spaces) over
    service_def = r"""
    def _service_exists(self, service_name: str) -> bool:
        if not service_name:
            return False
        success, _ = run_shell_captured(
            f"systemctl list-unit-files --type=service {service_name} 2>/dev/null | grep -q '^{service_name}\\s'",
            timeout=5,
        )
        return success"""
    service_idem = "def _service_exists(self,"
    # `service_def` needs to be inserted above `service_def_anchor`
    service_def_anchor = "def handle_temp_sensor(self,"
    # this will get inserted before the proxy routes are built in `AdsbIm` in the `__init()__` section
    # 2 indents (8 spaces) over
    proxy_def = """\
        self._d.env_by_tags("flightgazer_proxy").value = self._service_exists("flightgazer-webapp.service")
        if self._d.is_enabled("flightgazer_proxy") and not any(
            endpoint == "/flightgazer/" for endpoint, _, _ in self._d._proxy_routes
        ):
            self._d._proxy_routes.append(["/flightgazer/", "FLIGHTGAZER", "/flightgazer/"])"""
    proxy_idem = """self._d.env_by_tags("flightgazer_proxy").value = self._service_exists("flightgazer-webapp.service")"""
    # `proxy_def` needs to be inserted above `service_def_anchor`
    proxy_anchor = "self._routemanager.add_proxy_routes(self._d.proxy_routes)"
    service_def_anchor_line = anchor_finder(MAIN_FILE, service_def_anchor)
    proxy_anchor_line = anchor_finder(MAIN_FILE, proxy_anchor)
    if service_def_anchor_line is None or proxy_anchor_line is None:
        print("Could not find anchor points. No edits will be done.")
        raise ValueError
    check1 = anchor_finder(MAIN_FILE, service_idem)
    check2 = anchor_finder(MAIN_FILE, proxy_idem)
    file_owner = MAIN_FILE.owner()
    if check1 and check2:
        print(f"Found FlightGazer entries (lines {check1}, {check2}), not editing.")
        return False
    if not check1:
        inserter(MAIN_FILE, service_def, service_def_anchor_line - 1)
    if not check2:
        inserter(MAIN_FILE, proxy_def, proxy_anchor_line - 1)
    shutil.chown(MAIN_FILE, user=file_owner)
    check1 = anchor_finder(MAIN_FILE, service_idem)
    check2 = anchor_finder(MAIN_FILE, proxy_idem)
    print(f"Successfully inserted at line {check1} and {check2}")
    return True

def data_patcher() -> bool:
    print(f"> Patching {DATA_FILE.absolute()}...")
    # this will get inserted in the `_proxy_routes` list in the `Data` class,
    # 2 indents (8 spaces) over
    proxy_route_def = """        ["/flightgazer/", "FLIGHTGAZER", "/flightgazer/"],"""
    proxy_route_idem = """["/flightgazer/", "FLIGHTGAZER", "/flightgazer/"]"""
    # `proxy_route_def` needs to inserted below `proxy_anchor`
    proxy_anchor = """["/dump978/", "UAT978", "/skyaware978/"],"""
    # this will get inserted in the `_env` dict in the `Data` class,
    # 2 indents (8 spaces) over (not strictly necessary)
    env_route_def = """\
        Env("AF_FLIGHTGAZER_PORT", default=9898, tags=["flightgazerport", "norestore"]),
        Env("AF_IS_FLIGHTGAZER_PROXY_ENABLED", default=False, tags=["flightgazer_proxy", "is_enabled"]),"""
    env_route_anchor = """Env("AF_UAT978_PORT","""
    env_route_idem = """Env("AF_FLIGHTGAZER_PORT", default=9898,"""
    proxy_anchor_line = anchor_finder(DATA_FILE, proxy_anchor)
    env_anchor_line = anchor_finder(DATA_FILE, env_route_anchor)
    if proxy_anchor_line is None or env_anchor_line is None:
        print("Could not find anchor points. No edits will be done.")
        raise ValueError
    check1 = anchor_finder(DATA_FILE, proxy_route_idem)
    check2 = anchor_finder(DATA_FILE, env_route_idem)
    if check1 and check2:
        print(f"Found FlightGazer entries (lines {check1}, {check2}), not editing.")
        return False
    file_owner = DATA_FILE.owner()
    if not check1:
        inserter(DATA_FILE, proxy_route_def, proxy_anchor_line)
    if not check2:
        inserter(DATA_FILE, env_route_def, env_anchor_line + 1)
    shutil.chown(DATA_FILE, user=file_owner)
    check1 = anchor_finder(DATA_FILE, proxy_route_idem)
    check2 = anchor_finder(DATA_FILE, env_route_idem)
    print(f"Successfully inserted at lines {check1} and {check2}")
    return True

def html_patcher() -> bool:
    print(f"> Patching {HTML_FILE.absolute()}...")
    # this will get inserted in the `navbarDropdownLogs` node
    html_entry_def = """\
            {% if is_enabled('flightgazer_proxy') %}
            <li><a class="dropdown-item" href="/flightgazer/">FlightGazer</a></li>
            {% endif %}"""
    # `html_entry_def` needs to be added one line below `html_anchor`
    html_anchor = """<li><a class="dropdown-item" href="/skystats/">Skystats</a></li>"""
    html_idem = """<li><a class="dropdown-item" href="/flightgazer/">FlightGazer</a></li>"""
    anchor_line = anchor_finder(HTML_FILE, html_anchor)
    if anchor_line is None:
        print("Could not find anchor point. No edits will be done.")
        raise ValueError
    check1 = anchor_finder(HTML_FILE, html_idem)
    if check1 is not None:
        print(f"Found FlightGazer entry (line {check1}), not editing.")
        return False
    file_owner = HTML_FILE.owner()
    inserter(HTML_FILE, html_entry_def, anchor_line + 2)
    shutil.chown(HTML_FILE, user=file_owner)
    check1 = anchor_finder(HTML_FILE, html_idem)
    print(f"Successfully inserted at line {check1}")
    return True

print("Making backups...")
backups: list[Path] = []
for target in integrity:
    backupfile = target.with_suffix(target.suffix + ".bak")
    backups.append(backupfile)
    shutil.copy2(target.absolute(), backupfile.absolute())

try:
    results = [
        main_patcher(),
        data_patcher(),
        html_patcher()
    ]
except Exception as e:
    print("Error: Cannot continue, this patcher must be updated to handle newer adsb.im changes.")
    for backup in backups:
        shutil.copy2(backup.absolute(), backup.absolute().with_suffix(""))
        backup.unlink(missing_ok=True)
    sys.exit(1)

if any(results):
    print("Restarting adsb.im...")
    try:
        main_check = subprocess.run(["systemctl", "restart", "adsb-setup.service"], timeout=30, check=True)
        print("adsb.im started successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Failed to start adsb.im, reverting changes.")
        subprocess.run(["systemctl", "stop", "adsb-setup.service"])
        for backup in backups:
            shutil.copy2(backup.absolute(), backup.absolute().with_suffix(""))
            backup.unlink(missing_ok=True)
        try:
            subprocess.run(["systemctl", "start", "adsb-setup.service"], timeout=30, check=True)
        except subprocess.CalledProcessError as e:
            print("Uh oh, adsb.im isn't starting.")
            # maybe fire up the adsb.im updater again here
            sys.exit(1)
else:
    print("No changes made.")

for backup in backups:
    backup.unlink(missing_ok=True)
print("FlightGazer adsb.im patching script finished.")
sys.exit(0)