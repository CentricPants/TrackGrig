#!/bin/bash
# setup_auth.sh
# Creates mosquitto's password file with two service accounts:
#   backend_service - used by the FastAPI backend to subscribe/publish
#   device_client   - a shared credential used by all simulated devices
#
# Why a SHARED device credential instead of one per device?
# Mosquitto's built-in password file is static and reloaded on SIGHUP/restart;
# it has no API for provisioning per-device credentials on the fly, which is
# what you'd want for hundreds/thousands of real devices (you'd use a dynamic
# auth plugin, e.g. mosquitto-go-auth backed by a database, for that).
# For this project, MQTT-level auth answers "is this a legitimate fleet
# client at all?" while the application-level API key (see backend/models.py)
# answers "which specific device is this?". Two layers, two different jobs.
#
# CHANGE THESE PASSWORDS before using this anywhere but local development.

set -e
cd "$(dirname "$0")"

BACKEND_PW="${MQTT_BACKEND_PASSWORD:-backend-service-pw-change-me}"
DEVICE_PW="${MQTT_DEVICE_PASSWORD:-device-client-pw-change-me}"

rm -f passwd
mosquitto_passwd -c -b passwd backend_service "$BACKEND_PW"
mosquitto_passwd -b passwd device_client "$DEVICE_PW"

# Same privilege-drop issue as the certs: make sure the mosquitto service
# user can actually read the password file and ACL file once it de-roots.
if id -u mosquitto >/dev/null 2>&1; then
    chown mosquitto:mosquitto passwd acl.conf
    chmod 640 passwd
    chmod 600 acl.conf
    mkdir -p data
    chown mosquitto:mosquitto data
else
    echo "NOTE: no 'mosquitto' system user found - chown passwd/acl.conf/data"
    echo "to whichever user your broker drops privileges to."
fi

echo "Wrote $(pwd)/passwd with accounts: backend_service, device_client"
echo "Set MQTT_BACKEND_PASSWORD / MQTT_DEVICE_PASSWORD env vars before running"
echo "this script to use your own passwords instead of the defaults."
