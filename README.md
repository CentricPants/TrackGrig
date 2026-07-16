# TrackGrid — Simulated IoT Fleet Tracking System
ps: I had named this phantomtrack in the original repo, but I realized that was a bad name for a fleet-tracking system and also because it sounded too similar to Phantombox which is a different project, so I renamed it to TrackGrid. The code still has phantomtrack in some places, but the README and the repo name are now TrackGrid. 
A working, end-to-end simulation of a GPS fleet-tracking pipeline, with
production-style security and real-time delivery baked in (not bolted on):

```
[Simulated Devices] --MQTT/TLS+auth--> [Mosquitto Broker] --> [FastAPI Backend]
                                                                     |
                                                         [SQLite Database]
                                                                     |
                                                    [JWT-authenticated REST + WebSocket]
                                                                     |
                                                          [Web Dashboard (Leaflet)]
```

Every piece below has been built AND tested live (not just written and assumed
to work) - see section 10 for exactly what was verified and how.

## 1. What's new in this version

This version replaces four things that were left as "demo shortcuts" in the
first pass, with real implementations:

| Before | Now |
|---|---|
| Mosquitto: no auth, plaintext, anyone can read/write any topic | TLS-only (port 8883), username/password auth required, per-user ACLs restrict devices to publish-only on their own topics |
| JWT existed but no endpoint actually required it | `/devices`, `/devices/{id}/history`, `/alerts` all return 401 without a valid token; dashboard has a real login screen |
| Geofence was a circle (distance-from-center) | Real point-in-polygon (ray-casting) test against arbitrary-shaped restricted zones |
| Dashboard polled REST every 3s | Dashboard opens a WebSocket after login; the backend pushes location + alert events the instant they're processed |

## 2. Prerequisites

- Python 3.9+
- Mosquitto MQTT broker (with the standard `mosquitto-clients` tools for testing)
- OpenSSL (for generating local dev certificates)
- A modern web browser

## 3. Install dependencies

```bash
# System packages (Debian/Ubuntu)
sudo apt-get update && sudo apt-get install -y mosquitto mosquitto-clients openssl

# Python packages
cd backend
pip install -r requirements.txt
```

## 4. Set up the broker: TLS + auth + ACLs

This is a one-time setup step (re-run only if you want fresh certs/passwords).

```bash
cd mosquitto

# 1. Generate a self-signed CA + server certificate for TLS
chmod +x generate_certs.sh && ./generate_certs.sh

# 2. Create the two service accounts (backend_service, device_client)
chmod +x setup_auth.sh && ./setup_auth.sh
```

Both scripts print what they did and fix file ownership for you (see the
"gotcha" callout below). By default they use placeholder passwords -
override them first if this will run anywhere but your own machine:

```bash
export MQTT_BACKEND_PASSWORD="something-long-and-random"
export MQTT_DEVICE_PASSWORD="something-else-long-and-random"
./setup_auth.sh
```

### The gotcha that will bite you if you hand-roll this yourself
Mosquitto's Debian/Ubuntu package starts as root (to bind the port) and then
**drops privileges to a dedicated `mosquitto` system user** for everything
else - including reading your password file and TLS certs. If those files
are root-owned with restrictive permissions (which is what you'd naturally
do for a private key), Mosquitto will fail with a confusing
`Error: Unable to open pwfile` or similar, even though the file clearly
exists and root can read it fine with `cat`. Both setup scripts here chown
the relevant files to the `mosquitto` user automatically - if you regenerate
anything by hand, remember to redo that.

### Start the broker

```bash
cd mosquitto
mosquitto -c mosquitto.conf
```

Leave this running in its own terminal (or `mosquitto -c mosquitto.conf -d`
to daemonize). Verify it's enforcing auth:

```bash
# Should fail - no credentials
mosquitto_pub -h localhost -p 8883 --cafile certs/ca.crt -t test -m hi

# Should succeed
mosquitto_pub -h localhost -p 8883 --cafile certs/ca.crt \
  -u backend_service -P "<your password>" -t test -m hi
```

## 5. Start the backend

```bash
cd backend
uvicorn main:app --reload --port 8000
```

On startup this will:
- Bind the asyncio event loop so MQTT (a background thread) can safely push
  WebSocket broadcasts onto it
- Create `database/fleet.db` from `database/schema.sql` if it doesn't exist
- Connect to Mosquitto **over TLS with the `backend_service` credentials**
  and subscribe to `fleet/location/+`
- Expose the REST API and WebSocket endpoint on `http://localhost:8000`

Environment variables (all optional, sensible localhost defaults provided):

| Variable | Purpose |
|---|---|
| `PHANTOMTRACK_SECRET` | HMAC secret for signing JWTs - **override this in anything but local dev** |
| `MQTT_HOST` / `MQTT_PORT` | Where to find the broker (default `localhost:8883`) |
| `MQTT_BACKEND_USERNAME` / `MQTT_BACKEND_PASSWORD` | Broker credentials for the backend |
| `MQTT_CA_CERT` | Path to the CA cert used to verify the broker's TLS certificate |

Check it's alive: `curl http://localhost:8000/health` → `{"status":"ok"}`
(this one endpoint is intentionally unauthenticated as a liveness probe).

## 6. Start the device simulator

```bash
cd simulator
python3 device_simulator.py --devices 10
```

Each vehicle connects to the broker over TLS using the shared
`device_client` credential, then publishes to its own topic
(`fleet/location/veh_001`, etc.) with its individual API key in the payload
(see section 8 for why there are two separate credentials here).

Movement is now physically consistent with each vehicle's reported speed -
a well-behaved vehicle's actual displacement between pings always matches
`speed × elapsed_time`, so it will never accidentally trip the teleport
detector. Only vehicles you explicitly flag will misbehave:

```bash
python3 device_simulator.py --devices 10 --misbehave veh_002 veh_007
```

Flagged vehicles occasionally (30% chance per tick) either jump to a random
nearby-ish location (teleport) or spike their reported speed - useful for
demoing the anomaly engine live.

Same environment variables as the backend apply here for MQTT connection
details (`MQTT_HOST`, `MQTT_DEVICE_USERNAME`, `MQTT_DEVICE_PASSWORD`, etc).

## 7. Open the dashboard

Open `frontend/index.html` directly in a browser (or serve it with
`python3 -m http.server 8080` from inside `frontend/`).

You'll see a login screen first (demo credentials `admin` / `admin123`,
pre-filled for convenience - change `DASHBOARD_USER` in `backend/main.py`
for anything real). After logging in:

- The dashboard fetches the current device list and recent alerts once via
  REST, to seed the map immediately
- It then opens a WebSocket to `/ws?token=<jwt>` and receives every
  subsequent location update and alert **live**, no polling delay
- The JWT is kept in `localStorage` so you don't have to log in again on
  every page refresh (it's still subject to the same 60-minute expiry as
  any other use of the token)
- Clicking a vehicle in the sidebar pulls its full location history via the
  (also JWT-protected) `/devices/{id}/history` endpoint and redraws its
  trail from real stored data, not just what's accumulated since the page
  loaded

## 8. REST + WebSocket API reference

| Method | Endpoint | Auth required? | Description |
|--------|----------|:---:|---|
| POST | `/auth/token` | No | Get a JWT (demo user: `admin`/`admin123`) |
| GET | `/health` | No | Liveness check |
| GET | `/devices` | **Yes** | All known devices + last position |
| GET | `/devices/{id}/history?limit=` | **Yes** | Location history for one device |
| GET | `/alerts?limit=` | **Yes** | Recent anomaly alerts |
| WS | `/ws?token=<jwt>` | **Yes** | Live push of `{"event":"location",...}` and `{"event":"alert",...}` messages |

"Yes" means: send `Authorization: Bearer <token>` for REST, or `?token=`
as a query param for the WebSocket (browsers can't set custom headers
during the WS handshake, hence the query param).

## 9. Anomaly detection rules (`backend/anomaly.py`)

1. **Speed anomaly** — flags any ping reporting > 120 km/h.
2. **Teleportation** — compares distance vs. elapsed time since the
   device's previous ping; flags if the implied speed exceeds 300 km/h.
3. **Geofence violation** — a real point-in-polygon test (ray-casting
   algorithm) against arbitrary-shaped restricted zones defined in
   `RESTRICTED_ZONES`, not just a circle. Edit the vertex lists to match
   your own zones. Note the accuracy caveat in the code comments: this
   treats lat/lon as flat-plane coordinates, which is fine for city-scale
   zones but not for anything spanning hundreds of km.

No debounce/cooldown is implemented on alerts - a vehicle sitting above the
speed limit will generate a new alert on every single ping for as long as
it stays there. Worth adding if you take this further (e.g. "only alert
once per incident, not once per ping").

## 10. Security architecture: two independent layers

This project deliberately layers two different kinds of authentication that
answer two different questions:

- **MQTT-transport auth** (TLS + username/password, enforced by the broker
  itself via `mosquitto/acl.conf`) answers: *"Is this connection allowed to
  talk to the broker at all, and what topics can it touch?"* All simulated
  devices share one `device_client` credential restricted to publish-only
  on `fleet/location/#` - they cannot subscribe to anything, so a
  compromised device can't eavesdrop on other vehicles or read alerts.
- **Application-level API keys** (checked inside `backend/mqtt_client.py`,
  stored per-device in the `devices` table) answer: *"Given that this is
  a legitimate fleet client, which specific device is this ping actually
  from?"* This is what stops one device from spoofing another's `device_id`
  even though they share the same MQTT credential.
- **JWT auth** (`backend/main.py`) is a third, separate layer for
  *dashboard/API clients* reading the data back out - unrelated to either
  of the above, since dashboard users aren't devices.

Why not just issue one MQTT credential per device? Mosquitto's built-in
password file is static (reloaded on restart/SIGHUP), with no API for
provisioning credentials on the fly - fine for a fixed fleet, painful for
one that grows dynamically. A real large-scale deployment would swap this
for a dynamic auth plugin (e.g. `mosquitto-go-auth`) backed by a database,
so each device could get (and have revoked) its own broker-level identity
too.

## 11. Tested end-to-end

Everything below was actually run and verified, not just written:

- **TLS**: broker starts with `listener 8883` + cert/key, and the
  `mosquitto_pub`/`mosquitto_sub` gotcha (privilege drop to the `mosquitto`
  user) was hit, diagnosed with `strace`, and fixed.
- **Auth + ACLs**: connecting with no credentials → rejected; wrong
  password → rejected; `device_client` publishing to `fleet/location/#` →
  allowed; `device_client` publishing to `fleet/alerts` → silently dropped
  by the ACL (confirmed via a subscriber that never received it);
  `backend_service` publishing to `fleet/alerts` → delivered.
- **JWT lockdown**: `/devices` with no token → `401`; with a valid token →
  `200`; wrong login credentials → `400`.
- **WebSocket**: connecting without a token → rejected (handshake fails);
  connecting with a valid token → receives live `location` and `alert`
  events as the simulator publishes them, confirmed via a raw WebSocket
  client.
- **Polygon geofence**: a point inside the configured polygon correctly
  triggers `GEOFENCE`; a point clearly outside does not.
- **Movement physics fix**: after tying displacement to
  `speed × elapsed_time`, well-behaved (non-`--misbehave`) vehicles ran for
  60+ seconds with zero spurious `TELEPORT`/`SPEED` alerts, while flagged
  vehicles reliably triggered them.
- **Full frontend flow**, exercised with a jsdom-based harness against the
  live backend: no stored token → login screen shown; correct credentials →
  dashboard loads, device list populates, WebSocket delivers live
  updates; incorrect credentials → error shown, dashboard stays hidden;
  logout → returns to login screen.

## 12. Project structure

```
phantomtrack/
├── simulator/
│   └── device_simulator.py   # Simulates N vehicles over MQTT/TLS with physics-consistent movement
├── backend/
│   ├── main.py                # FastAPI app: JWT-protected REST + WebSocket endpoint
│   ├── mqtt_client.py          # TLS MQTT subscriber, validation, WebSocket broadcast bridge
│   ├── anomaly.py              # Speed / teleport / polygon geofence detection rules
│   ├── models.py               # SQLite data access layer
│   ├── ws_manager.py           # WebSocket connection manager (thread-safe broadcast bridge)
│   └── requirements.txt
├── frontend/
│   ├── index.html              # Dashboard shell + login screen
│   └── map.js                  # Leaflet map, login flow, WebSocket live updates
├── mosquitto/
│   ├── mosquitto.conf           # TLS listener, auth, ACL config
│   ├── acl.conf                 # Per-user topic restrictions
│   ├── generate_certs.sh        # Self-signed CA + server cert generation
│   ├── setup_auth.sh            # Password file creation for the two service accounts
│   └── certs/                   # Generated certs live here (not committed)
├── database/
│   └── schema.sql              # Devices / locations / alerts tables
└── README.md
```

## 13. Extending this project further

- Swap SQLite for PostgreSQL (the SQL in `models.py` is already standard
  enough to port with minimal changes) - needed once you have enough
  concurrent writers that SQLite's single-writer lock becomes a bottleneck.
- Add alert debounce/cooldown so a sustained speeding incident doesn't
  flood the `alerts` table with one row per ping.
- Move from a shared `device_client` MQTT credential to per-device broker
  credentials via a dynamic auth plugin, for real revocability at the
  transport layer.
- Add a proper user store (hashed passwords in the database) instead of
  the hardcoded `DASHBOARD_USER` dict.
- Containerize with Docker Compose (one service each for Mosquitto,
  backend, and a static file server for the frontend) so the whole stack
  comes up with one command.

