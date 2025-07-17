#!/bin/bash
{
# Script to update FlightGazer's web interface
# Last updated: v.0.4.0
# by: WeegeeNumbuh1
BASEDIR=$(cd `dirname -- $0` && pwd)
TEMPPATH=/tmp/FlightGazer-tmp
GREEN='\033[0;32m'
ORANGE='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -ne "\033]0;FlightGazer Web Interface Updater\007" # set window title
echo -e "\n${ORANGE}>>> FlightGazer Web Interface update script started."
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

rm -rf ${TEMPPATH} >/dev/null 2>&1 # make sure the temp directory doesn't exist before we start
echo -e "${GREEN}>>> Downloading latest version...${NC}${FADE}"
git clone --depth=1 https://github.com/WeegeeNumbuh1/FlightGazer-webapp $TEMPPATH
if [ $? -ne 0 ]; then
	rm -rf ${TEMPPATH} >/dev/null 2>&1
	echo -e "${RED}>>> ERROR: Failed to download from GitHub. Installer cannot continue.${NC}"
	exit 1
fi
rm -rf ${TEMPPATH}/.git >/dev/null 2>&1
find "${TEMPPATH}" -type f -name '.*' -exec rm '{}' \; >/dev/null 2>&1
if [ -f "${TEMPPATH}/version-webapp" ]; then
	VER_STR=$(head -c 12 ${TEMPPATH}/version-webapp)
	echo -e "${NC}> Downloaded FlightGazer-webapp version: ${VER_STR}"
fi

read -r OWNER_OF_FGDIR GROUP_OF_FGDIR <<<$(stat -c "%U %G" ${BASEDIR})
chown -Rf ${OWNER_OF_FGDIR}:${GROUP_OF_FGDIR} ${TEMPPATH} # need to do this as we are running as root
echo -e "${FADE}Copying ${TEMPPATH} to ${BASEDIR}/web-app..."
cp -afT ${TEMPPATH} ${BASEDIR} # recall that this script already lives in the web-app folder
echo "> Restarting service..."
rm -rf ${TEMPPATH} >/dev/null 2>&1 # clean up after ourselves
systemctl restart flightgazer-webapp.service
if [ $? -ne 0 ]; then
    echo -e "${RED}>>> Failed to start service!"
    exit 1
else
    echo -e "${GREEN}>>> Update complete."
    exit 0
fi
}