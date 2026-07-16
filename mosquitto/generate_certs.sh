#!/bin/bash
# generate_certs.sh
# Creates a self-signed Certificate Authority and a server certificate for
# Mosquitto's TLS listener. This is sufficient for local development and
# demos. For a real deployment, use certs from a real CA (or an internal
# CA like step-ca / Vault PKI) and rotate them - these are NOT meant to be
# used in production as-is.

set -e
cd "$(dirname "$0")/certs"

echo "== Generating CA key + self-signed CA certificate =="
openssl genrsa -out ca.key 2048
openssl req -x509 -new -nodes -key ca.key -sha256 -days 3650 \
    -subj "/CN=PhantomTrack-Demo-CA" \
    -out ca.crt

echo "== Generating server key + certificate signing request =="
openssl genrsa -out server.key 2048
openssl req -new -key server.key \
    -subj "/CN=localhost" \
    -out server.csr

echo "== Signing server certificate with our CA =="
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out server.crt -days 825 -sha256 \
    -extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1")

chmod 644 ca.crt server.crt
chmod 600 ca.key server.key

rm -f server.csr

# IMPORTANT: on most distros (including Debian/Ubuntu's mosquitto package),
# the broker starts as root to bind the port, then drops privileges to a
# dedicated "mosquitto" system user for everything else - including reading
# these cert files. If they stay root-owned with 600 perms, the broker will
# fail with a confusing EACCES once it drops privileges. Fix ownership so
# the mosquitto user can actually read what it needs, while the CA/server
# private keys stay unreadable to everyone else.
if id -u mosquitto >/dev/null 2>&1; then
    chown mosquitto:mosquitto ca.crt server.crt server.key
    chmod 640 server.key
    echo "Ownership set for the 'mosquitto' system user."
else
    echo "NOTE: no 'mosquitto' system user found on this machine - if your"
    echo "broker drops privileges to a different user, chown these files to it."
fi

echo ""
echo "Done. Files created in $(pwd):"
ls -la
echo ""
echo "ca.crt   -> distribute to any client that needs to verify the broker"
echo "server.* -> used by Mosquitto itself, keep server.key private"
