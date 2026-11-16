#!/usr/bin/env python3
"""LC29H-BS RTCM forwarder and small local API for the Rover dashboard."""

import asyncio
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

import serial


SERIAL_PORT = os.environ.get("BASE_SERIAL_PORT", "/dev/serial0")
BAUD_RATE = int(os.environ.get("BASE_BAUD_RATE", "115200"))
RTCM_PORT = int(os.environ.get("BASE_RTCM_PORT", "5000"))
HTTP_PORT = int(os.environ.get("BASE_HTTP_PORT", "8080"))
BIND_HOST = os.environ.get("BASE_BIND_HOST", "0.0.0.0")
DB_PATH = Path(os.environ.get("BASE_DB_PATH", "/home/antenna/Documents/base_state.sqlite3"))
ROVER_API_TOKEN = os.environ.get("ROVER_API_TOKEN", "").strip()
ALLOWED_ORIGINS = {
    value.strip()
    for value in os.environ.get("ALLOWED_ORIGINS", "*").split(",")
    if value.strip()
}
ONLINE_TIMEOUT = float(os.environ.get("ROVER_ONLINE_TIMEOUT", "5"))
MAX_RTCM_FRAME = 1023

rover_clients = set()
base_state = {
    "lat": None, "lon": None, "alt": None, "source": None,
    "quality": None, "updated_at": None,
}
rover_state = {
    "lat": None, "lon": None, "alt": None, "satellites": None,
    "rtk_status": "NO DATA", "fix_timestamp": None, "last_seen": None,
    "speed_kmh": None, "heading": None, "pitch": None, "roll": None,
    "yaw": None, "rtcm": {},
}


def db_connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    return connection


def persist_value(key, value):
    connection = db_connect()
    try:
        connection.execute(
            "INSERT INTO state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, separators=(",", ":"))),
        )
        connection.commit()
    finally:
        connection.close()


def load_persisted_state():
    with db_connect() as connection:
        rows = dict(connection.execute("SELECT key, value FROM state"))
    for key, target in (("base", base_state), ("rover", rover_state)):
        if key in rows:
            try:
                target.update(json.loads(rows[key]))
            except (TypeError, ValueError):
                print(f"[DB] Stato {key} non leggibile; uso valori vuoti.")


def save_state(key, value):
    persist_value(key, value)


def crc24q(data):
    crc = 0
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1864CFB) if crc & 0x800000 else crc << 1
            crc &= 0xFFFFFF
    return crc


def read_bits(data, start, length):
    value = 0
    for bit_index in range(start, start + length):
        value = (value << 1) | ((data[bit_index // 8] >> (7 - bit_index % 8)) & 1)
    return value


def signed(value, bit_length):
    sign_bit = 1 << (bit_length - 1)
    return value - (1 << bit_length) if value & sign_bit else value


def ecef_to_geodetic(x, y, z):
    a = 6378137.0
    e2 = 6.69437999014e-3
    p = math.hypot(x, y)
    if p == 0:
        return None
    lon = math.atan2(y, x)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(12):
        sin_lat = math.sin(lat)
        n = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
        lat = math.atan2(z + e2 * n * sin_lat, p)
    sin_lat = math.sin(lat)
    n = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
    alt = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), alt


def update_base_position(lat, lon, alt, source, quality=None):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return
    base_state.update({
        "lat": float(lat), "lon": float(lon),
        "alt": None if alt is None else float(alt),
        "source": source, "quality": quality, "updated_at": time.time(),
    })
    save_state("base", base_state)


def parse_rtcm_position(frame):
    payload = frame[3:-3]
    try:
        message_type = read_bits(payload, 0, 12)
        if message_type not in (1005, 1006):
            return
        x = signed(read_bits(payload, 34, 38), 38) * 0.0001
        y = signed(read_bits(payload, 74, 38), 38) * 0.0001
        z = signed(read_bits(payload, 114, 38), 38) * 0.0001
        result = ecef_to_geodetic(x, y, z)
        if result:
            update_base_position(*result, source=f"RTCM {message_type}")
            print(f"[BASE] Posizione da RTCM {message_type}: {result[0]:.7f}, {result[1]:.7f}")
    except (IndexError, ValueError, OverflowError):
        return


def parse_nmea_coordinate(value, hemisphere, degree_digits):
    degrees = int(value[:degree_digits])
    minutes = float(value[degree_digits:])
    coordinate = degrees + minutes / 60.0
    return -coordinate if hemisphere in ("S", "W") else coordinate


def parse_gga(sentence):
    try:
        text = sentence.decode("ascii", errors="ignore").strip()
        if not text.startswith("$"):
            return
        body = text[1:].split("*", 1)[0]
        if "*" in text:
            checksum_text = text.split("*", 1)[1][:2]
            checksum = 0
            for char in body:
                checksum ^= ord(char)
            if not checksum_text or checksum != int(checksum_text, 16):
                return
        fields = body.split(",")
        if len(fields) < 10 or not fields[0].endswith("GGA"):
            return
        quality = int(fields[6] or "0")
        if quality <= 0 or not fields[2] or not fields[4]:
            return
        lat = parse_nmea_coordinate(fields[2], fields[3], 2)
        lon = parse_nmea_coordinate(fields[4], fields[5], 3)
        alt = float(fields[9]) if fields[9] else None
        if base_state["source"] not in ("RTCM 1005", "RTCM 1006"):
            update_base_position(lat, lon, alt, "NMEA GGA", quality)
    except (ValueError, IndexError):
        return


def extract_frames(buffer):
    frames = []
    while buffer:
        rtcm_index = buffer.find(b"\xD3")
        nmea_index = buffer.find(b"$")
        indexes = [index for index in (rtcm_index, nmea_index) if index >= 0]
        if not indexes:
            buffer.clear()
            break
        first = min(indexes)
        if first:
            del buffer[:first]
        if buffer[0] == 0xD3:
            if len(buffer) < 3:
                break
            if buffer[1] & 0xFC:
                del buffer[0]
                continue
            length = ((buffer[1] & 0x03) << 8) | buffer[2]
            if length > MAX_RTCM_FRAME:
                del buffer[0]
                continue
            total = 3 + length + 3
            if len(buffer) < total:
                break
            frame = bytes(buffer[:total])
            if int.from_bytes(frame[-3:], "big") != crc24q(frame[:-3]):
                del buffer[0]
                continue
            parse_rtcm_position(frame)
            frames.append(frame)
            del buffer[:total]
        else:
            newline = buffer.find(b"\n")
            if newline < 0:
                if len(buffer) > 256:
                    del buffer[0]
                    continue
                break
            parse_gga(bytes(buffer[:newline + 1]))
            del buffer[:newline + 1]
    return frames


async def serial_reader_task():
    while True:
        try:
            print(f"[SERIALE] Apro {SERIAL_PORT} a {BAUD_RATE} baud")
            with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1) as port:
                buffer = bytearray()
                print("[SERIALE] Ricevitore Base collegato")
                while True:
                    chunk = await asyncio.to_thread(port.read, 1024)
                    if not chunk:
                        continue
                    buffer.extend(chunk)
                    for frame in extract_frames(buffer):
                        await forward_rtcm(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[SERIALE] {exc}; nuovo tentativo tra 3 secondi")
            await asyncio.sleep(3)


async def forward_rtcm(frame):
    for writer in list(rover_clients):
        try:
            writer.write(frame)
            await writer.drain()
        except (ConnectionError, OSError):
            rover_clients.discard(writer)
            writer.close()


async def handle_rover(reader, writer):
    peer = writer.get_extra_info("peername")
    rover_clients.add(writer)
    print(f"[RTCM] Rover connesso: {peer}")
    try:
        while await reader.read(1024):
            pass
    except (ConnectionError, OSError):
        pass
    finally:
        rover_clients.discard(writer)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        print(f"[RTCM] Rover disconnesso: {peer}")


def public_state():
    now = time.time()
    base = dict(base_state)
    base["available"] = base["lat"] is not None and base["lon"] is not None
    base["age_seconds"] = None if base["updated_at"] is None else round(now - base["updated_at"], 2)
    rover = dict(rover_state)
    rover["online"] = rover["last_seen"] is not None and now - rover["last_seen"] <= ONLINE_TIMEOUT
    rover["age_seconds"] = None if rover["last_seen"] is None else round(now - rover["last_seen"], 2)
    fix_at = rover.get("fix_timestamp")
    rover["fix_age_seconds"] = None if fix_at is None else round(now - float(fix_at), 2)
    if rover["fix_age_seconds"] is None or rover["fix_age_seconds"] > 3:
        rover["rtk_status"] = "NO FIX"
        rover["satellites"] = 0
    return {
        "type": "telemetry", "timestamp": int(now * 1000),
        "base": base, "rover": rover,
    }


def valid_coordinate_pair(lat, lon):
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return lat, lon


def update_rover(payload):
    now = time.time()
    coordinates = valid_coordinate_pair(payload.get("lat"), payload.get("lon"))
    if coordinates:
        rover_state["lat"], rover_state["lon"] = coordinates
        try:
            rover_state["alt"] = float(payload.get("alt")) if payload.get("alt") is not None else None
        except (TypeError, ValueError):
            rover_state["alt"] = None
        rover_state["fix_timestamp"] = payload.get("fix_timestamp") or now
    for key in ("satellites", "rtk_status", "speed_kmh", "heading", "pitch", "roll", "yaw", "rtcm"):
        if key in payload:
            rover_state[key] = payload[key]
    rover_state["last_seen"] = now
    save_state("rover", rover_state)


def response_bytes(status, body=b"", content_type="application/json; charset=utf-8", origin="*"):
    reason = {
        200: "OK", 202: "Accepted", 204: "No Content", 400: "Bad Request",
        401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
        405: "Method Not Allowed", 411: "Length Required", 413: "Payload Too Large",
        415: "Unsupported Media Type", 500: "Internal Server Error", 503: "Service Unavailable",
    }.get(status, "Error")
    headers = [
        f"HTTP/1.1 {status} {reason}",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
        "Cache-Control: no-store",
        "Connection: close",
        f"Access-Control-Allow-Origin: {origin}",
        "Access-Control-Allow-Methods: GET, POST, OPTIONS",
        "Access-Control-Allow-Headers: Content-Type, Authorization",
        "Vary: Origin",
        "",
        "",
    ]
    return "\r\n".join(headers).encode("ascii") + body


async def handle_http(reader, writer):
    try:
        raw_headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
        if len(raw_headers) > 32768:
            writer.write(response_bytes(413, b"{}"))
            await writer.drain()
            return
        lines = raw_headers.decode("iso-8859-1").split("\r\n")
        request_parts = lines[0].split()
        if len(request_parts) < 2:
            writer.write(response_bytes(400, b"{}"))
            await writer.drain()
            return
        method, target = request_parts[0], request_parts[1]
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        origin = headers.get("origin", "")
        allow_all_origins = "*" in ALLOWED_ORIGINS
        if not allow_all_origins and origin and origin not in ALLOWED_ORIGINS:
            writer.write(response_bytes(403, b"{\"error\":\"origin not allowed\"}", origin="null"))
            await writer.drain()
            return
        cors_origin = "*" if allow_all_origins else (origin or next(iter(ALLOWED_ORIGINS), "null"))

        if method == "OPTIONS":
            writer.write(response_bytes(204, b"", "text/plain", cors_origin))
            await writer.drain()
            return
        path = urlsplit(target).path
        if method == "GET" and path in ("/api/state", "/healthz"):
            state = public_state() if path == "/api/state" else {"ok": True}
            body = json.dumps(state, separators=(",", ":")).encode("utf-8")
            writer.write(response_bytes(200, body, origin=cors_origin))
        elif method == "POST" and path == "/api/rover":
            if not ROVER_API_TOKEN:
                writer.write(response_bytes(503, b"{\"error\":\"ROVER_API_TOKEN non configurato\"}", origin=cors_origin))
            elif headers.get("authorization") != f"Bearer {ROVER_API_TOKEN}":
                writer.write(response_bytes(401, b"{\"error\":\"unauthorized\"}", origin=cors_origin))
            elif "content-length" not in headers:
                writer.write(response_bytes(411, b"{\"error\":\"content length required\"}", origin=cors_origin))
            else:
                length = int(headers["content-length"])
                if length > 16384:
                    writer.write(response_bytes(413, b"{\"error\":\"payload too large\"}", origin=cors_origin))
                else:
                    body = await asyncio.wait_for(reader.readexactly(length), timeout=5)
                    payload = json.loads(body.decode("utf-8"))
                    if not isinstance(payload, dict):
                        writer.write(response_bytes(400, b"{\"error\":\"object required\"}", origin=cors_origin))
                    else:
                        update_rover(payload)
                        writer.write(response_bytes(202, b"{}", origin=cors_origin))
        else:
            writer.write(response_bytes(404, b"{\"error\":\"not found\"}", origin=cors_origin))
        await writer.drain()
    except asyncio.IncompleteReadError:
        pass
    except asyncio.TimeoutError:
        pass
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        try:
            writer.write(response_bytes(400, b"{\"error\":\"invalid request\"}"))
            await writer.drain()
        except Exception:
            pass
    except (ConnectionError, OSError):
        pass
    finally:
        if not writer.is_closing():
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def main():
    load_persisted_state()
    if not ROVER_API_TOKEN:
        print("[AVVISO] ROVER_API_TOKEN non configurato: POST /api/rover risponderà 503.")
    rtcm_server = await asyncio.start_server(handle_rover, BIND_HOST, RTCM_PORT)
    http_server = await asyncio.start_server(handle_http, BIND_HOST, HTTP_PORT)
    print(f"[BASE] RTCM TCP su {BIND_HOST}:{RTCM_PORT}")
    print(f"[BASE] API HTTP su {BIND_HOST}:{HTTP_PORT}/api/state")
    serial_task = asyncio.create_task(serial_reader_task())
    try:
        async with rtcm_server, http_server:
            await asyncio.gather(rtcm_server.serve_forever(), http_server.serve_forever())
    finally:
        serial_task.cancel()
        await asyncio.gather(serial_task, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[BASE] Arresto richiesto.")
