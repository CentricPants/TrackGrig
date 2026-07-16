"""
mqtt_client.py
Subscribes to fleet/location/<device_id> topics on the Mosquitto broker over
TLS, validates+stores each ping, runs anomaly detection, publishes any
resulting alerts back to fleet/alerts, and pushes both location updates and
alerts out to connected dashboard clients over WebSocket in real time.
"""

import json
import os
import threading
import paho.mqtt.client as mqtt

from models import register_device, verify_device_key, upsert_location, get_last_location, insert_alert
from anomaly import evaluate
from ws_manager import manager

# --- connection config ---------------------------------------------------
# Overridable via environment variables so you're not forced to hardcode
# credentials in source. Defaults match mosquitto/setup_auth.sh's defaults.
MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "8883"))
MQTT_USERNAME = os.environ.get("MQTT_BACKEND_USERNAME", "backend_service")
MQTT_PASSWORD = os.environ.get("MQTT_BACKEND_PASSWORD", "backend-service-pw-change-me")
MQTT_CA_CERT = os.environ.get(
    "MQTT_CA_CERT",
    os.path.join(os.path.dirname(__file__), "..", "mosquitto", "certs", "ca.crt"),
)

TOPIC_LOCATION = "fleet/location/+"
TOPIC_ALERTS = "fleet/alerts"

# In-memory broadcast hook so the REST API layer can serve the "latest known
# position" without hitting the DB for very hot paths, if needed later.
_latest_lock = threading.Lock()
_latest_pings = {}


def get_latest_pings():
    with _latest_lock:
        return dict(_latest_pings)


def _on_connect(client, userdata, flags, rc, properties=None):
    print(f"[mqtt] connected to broker over TLS rc={rc}")
    client.subscribe(TOPIC_LOCATION)


def _on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        print(f"[mqtt] rejected malformed payload on {msg.topic}")
        return

    device_id = payload.get("device_id")
    lat = payload.get("lat")
    lon = payload.get("lon")
    speed = payload.get("speed")
    timestamp = payload.get("timestamp")
    api_key = payload.get("api_key")

    # --- input validation: reject anything missing or out of range ---
    if not all([device_id, isinstance(lat, (int, float)), isinstance(lon, (int, float)),
                isinstance(speed, (int, float)), timestamp]):
        print(f"[mqtt] rejected incomplete payload from {msg.topic}")
        return
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or speed < 0:
        print(f"[mqtt] rejected out-of-range payload from {device_id}")
        return

    # --- application-level device auth ---
    # This is a SECOND, independent layer on top of the MQTT-transport auth
    # (username/password + TLS) that got this message to the broker at all.
    # MQTT auth proves "this is a legitimate fleet client"; this API key
    # proves "this is specifically device_id, not some other device
    # impersonating it" - the shared device_client MQTT credential alone
    # can't tell devices apart from each other.
    if not api_key:
        print(f"[mqtt] rejected {device_id}: missing api_key")
        return
    register_device(device_id)  # no-op if device already exists
    if not verify_device_key(device_id, api_key):
        print(f"[mqtt] rejected {device_id}: invalid api_key")
        return

    prev_row = get_last_location(device_id)
    upsert_location(device_id, lat, lon, speed, timestamp)

    with _latest_lock:
        _latest_pings[device_id] = {"lat": lat, "lon": lon, "speed": speed, "timestamp": timestamp}

    # Push the location update to every connected dashboard immediately,
    # instead of making them wait for their next poll.
    manager.broadcast_from_thread({
        "event": "location",
        "device_id": device_id,
        "lat": lat,
        "lon": lon,
        "speed": speed,
        "timestamp": timestamp,
    })

    alerts = evaluate(prev_row, lat, lon, speed, timestamp)
    for alert in alerts:
        insert_alert(device_id, alert["type"], alert["description"], timestamp)
        alert_payload = {
            "device_id": device_id,
            "type": alert["type"],
            "description": alert["description"],
            "timestamp": timestamp,
        }
        client.publish(TOPIC_ALERTS, json.dumps(alert_payload))
        manager.broadcast_from_thread({"event": "alert", **alert_payload})
        print(f"[alert] {alert_payload}")


def start_mqtt_client_background():
    """Start the MQTT subscriber loop on a background thread, connected
    over TLS with username/password auth."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="phantomtrack-backend")
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.tls_set(ca_certs=MQTT_CA_CERT)
    client.on_connect = _on_connect
    client.on_message = _on_message
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    thread = threading.Thread(target=client.loop_forever, daemon=True)
    thread.start()
    return client
