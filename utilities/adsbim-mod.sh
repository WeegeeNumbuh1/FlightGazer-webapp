#!/bin/bash
# get adsb.im images/apps to run the FlightGazer proxy patcher script
# Last updated: v.1.1.1

BASEDIR="$(cd "$(dirname -- "$0")" && pwd)"
SERVICE_FILE_DIR=/etc/systemd/system/flightgazer-proxy.service
if [ $(id -u) -ne 0 ]; then
	echo "This script must be run as root."
	exit 1
fi

service_file() {
	cat <<- EOF > $SERVICE_FILE_DIR
	[Unit]
	Description=Runs FlightGazer adsbim proxy patcher after adsb-im update
	Documentation="https://github.com/WeegeeNumbuh1/FlightGazer-webapp"
	After=adsb-feeder-update.service

	[Service]
	User=root
	Type=oneshot
	ExecStart=/usr/bin/python3 "${BASEDIR}/adsbim-proxy.py"

	[Install]
	WantedBy=adsb-feeder-update.service

	EOF

	systemctl daemon-reload >/dev/null 2>&1
	systemctl enable flightgazer-proxy.service >/dev/null 2>&1
}

systemctl list-unit-files adsb-feeder-update.service >/dev/null 2>&1
if [ $? -eq 0 ] && [ -f "${BASEDIR}/adsbim-proxy.py" ]; then
	service_file
	/usr/bin/python3 "${BASEDIR}/adsbim-proxy.py" >/dev/null 2>&1
fi
exit 0