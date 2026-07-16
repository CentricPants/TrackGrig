-- PhantomTrack Fleet Tracking System - Database Schema
-- SQLite schema for devices, location history, and alerts

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT UNIQUE NOT NULL,
    api_key TEXT NOT NULL,
    last_seen TEXT,
    last_lat REAL,
    last_lon REAL,
    last_speed REAL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    speed REAL NOT NULL,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (device_id) REFERENCES devices(device_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    type TEXT NOT NULL,          -- SPEED, TELEPORT, GEOFENCE
    description TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (device_id) REFERENCES devices(device_id)
);

CREATE INDEX IF NOT EXISTS idx_locations_device ON locations(device_id);
CREATE INDEX IF NOT EXISTS idx_locations_timestamp ON locations(timestamp);
CREATE INDEX IF NOT EXISTS idx_alerts_device ON alerts(device_id);
