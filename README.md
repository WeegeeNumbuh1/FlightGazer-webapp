<!-- Title -->
<div align="center">
    <a href="https://github.com/WeegeeNumbuh1/FlightGazer-webapp">
    <img src="static/FlightGazer-logo_webapp.png" alt="Logo">
    </a>
    <h1 align="center">FlightGazer Web App</h1>
    Web interface for <a href="https://github.com/WeegeeNumbuh1/FlightGazer">FlightGazer</a>.
</div>
<!-- end title section -->

## About
Repo for the web interface that handles configuration, reading logs, checking/installing updates, and changing startup options for FlightGazer.

> [!IMPORTANT]
> This won't do anything if FlightGazer isn't installed in your system!

## Install
Use the `install-FlightGazer-webapp.sh` script in the FlightGazer directory.

## Accessing the Webpage
Visit:
```
http://<your-device's-IP>/flightgazer
# or
http://<name-of-device-running-FlightGazer>.local/flightgazer

# If you don't have a web server on the system, you can access the configuration page directly on port 9898
```

## Update
Use the `install-FlightGazer-webapp.sh` file again once the web app is installed to check for updates and install the latest version here in this repo.<br>
The `update.sh` script for FlightGazer also handles updating the web interface as well and this is the preferred method.

## Uninstall
Use the `uninstall-webapp.sh` script in the `web-app` folder in FlightGazer directory.

## Changelog
[`Changelog-webapp.txt`](/Changelog-webapp.txt)

## Contributions
Pull requests are not accepted.<br>
Issues, bug reports, and other comments are welcomed.

## License & Warranty
Inherits the same license that FlightGazer uses. (GPLv3)<br>
Zero warranty provided.<br>
The javascript and css files in the [`static`](/static/) directory are distributed under the MIT License.