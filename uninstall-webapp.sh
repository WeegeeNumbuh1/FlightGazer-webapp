#!/bin/bash
{
# Uninstall script for FlightGazer's web interface
# Last updated: v.0.4.0
# by: WeegeeNumbuh1
BASEDIR=$(cd `dirname -- $0` && pwd)
GREEN='\033[0;32m'
ORANGE='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -ne "\033]0;FlightGazer Web Interface Uninstaller\007" # set window title
echo -e "\n${ORANGE}>>> FlightGazer web interface uninstall script started."
echo -e "${GREEN}>>> Setting up...${NC}"
if [ `id -u` -ne 0 ]; then
	>&2 echo -e "${RED}>>> ERROR: This script must be run as root.${NC}"
	sleep 1s
	exit 1
fi

if [ ! -f "${BASEDIR}/FG-webapp.py" ]; then
	echo -e "\n${NC}${RED}>>> ERROR: Cannot find FG-webapp.py. This installer script must be in the same directory as FG-webapp.py!${NC}"
	sleep 2s
	exit 1
fi

echo "> Removing systemd service..."
systemctl stop flightgazer-webapp.service
systemctl disable flightgazer-webapp.service
rm -f /etc/systemd/system/flightgazer-webapp.service 2>&1
systemctl daemon-reload 2>&1
systemctl reset-failed 2>&1

if command -v nginx >/dev/null 2>&1; then
	echo "> Removing nginx FlightGazer webapp config..."
	rm -f /etc/nginx/sites-enabled/flightgazer-webapp
	rm -f /etc/nginx/sites-available/flightgazer-webapp
	nginx -t && systemctl reload nginx
	echo "> nginx config removed."
fi

if command -v apache2 >/dev/null 2>&1; then
	echo "> Removing Apache FlightGazer webapp config..."
	a2dissite flightgazer-webapp
	rm -f /etc/apache2/sites-available/flightgazer-webapp.conf
	systemctl reload apache2
	echo "> Apache config removed."
fi

if command -v lighttpd >/dev/null 2>&1; then
    echo "> Removing Lighttpd FlightGazer webapp config..."
    lighttpd-disable-mod proxy
    lighttpd-disable-mod flightgazer-webapp
    rm -f /etc/lighttpd/conf-available/98-flightgazer-webapp.conf
    systemctl restart lighttpd
    echo "> Lighttpd config removed."
fi

echo "> Removing files..."
rm -rf ${BASEDIR}
echo -e "${GREEN}>>> FlightGazer web interface fully removed from your system.${NC}"
echo ""
exit 0
}