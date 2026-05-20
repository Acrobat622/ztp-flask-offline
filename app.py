"""
ZTP proxy: resolve device serial to hostname via a local CSV mapping,
then serve the corresponding NVUE config file (JSON or YAML) for
direct use with 'nv config patch'.
"""

from __future__ import annotations

import csv
import logging
import logging.handlers
import os
from typing import Any

from flask import Flask, Response, jsonify, request, send_file

ZTP_SCRIPT_PATH = os.environ.get("ZTP_SCRIPT_PATH", "/app/scripts/ztp-flask.sh")
IMAGES_DIR = os.environ.get("ZTP_IMAGES_DIR", "/app/images")
CONFIGS_DIR = os.environ.get("ZTP_CONFIGS_DIR", "/app/configs")
MAPPING_CSV = os.path.join(CONFIGS_DIR, "mapping.csv")
LOG_DIR = os.environ.get("ZTP_LOG_DIR", "/app/logs")
LOG_FILE = os.path.join(LOG_DIR, "ztp-proxy.log")

app = Flask(__name__)

_log_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")

_stream = logging.StreamHandler()
_stream.setFormatter(_log_fmt)

os.makedirs(LOG_DIR, exist_ok=True)
_file = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3)
_file.setFormatter(_log_fmt)

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.INFO)
LOG.addHandler(_stream)
LOG.addHandler(_file)


def _lookup_hostname(serial: str) -> str | None:
    """Read mapping.csv on every call and return the hostname for the given serial."""
    try:
        with open(MAPPING_CSV, newline="") as fh:
            for row in csv.DictReader(fh):
                if (row.get("Serial") or "").strip() == serial:
                    hostname = (row.get("Hostname") or "").strip()
                    return hostname if hostname else None
    except FileNotFoundError:
        LOG.error("Mapping CSV not found: %s", MAPPING_CSV)
    except Exception:
        LOG.exception("Failed to read mapping CSV: %s", MAPPING_CSV)
    return None


def _check_proxy_auth() -> tuple[Any, int] | None:
    expected = os.environ.get("ZTP_PROXY_API_KEY", "").strip()
    if not expected:
        return None
    got = request.headers.get("X-ZTP-Proxy-Key", "")
    if got != expected:
        return jsonify({"error": "unauthorized"}), 401
    return None


@app.get("/health")
def health() -> tuple[dict[str, str], int]:
    return {"status": "ok"}, 200


@app.get("/ztp/script")
def ztp_script() -> Response | tuple[Any, int]:
    if not os.path.isfile(ZTP_SCRIPT_PATH):
        return jsonify({"error": "script_not_found"}), 404
    return send_file(ZTP_SCRIPT_PATH, mimetype="text/x-shellscript")


@app.get("/images/<path:filename>")
def serve_image(filename: str) -> Response | tuple[Any, int]:
    safe = os.path.basename(filename)
    if safe != filename:
        return jsonify({"error": "invalid_filename"}), 400
    filepath = os.path.join(IMAGES_DIR, safe)
    if not os.path.isfile(filepath):
        LOG.warning("Image file not found: %s", safe)
        return jsonify({"error": "image_not_found", "filename": safe}), 404
    
    hostip = request.remote_addr or "unknown"
    LOG.info("Serving image file: %s to %s", safe, hostip)
    return send_file(filepath, mimetype="application/octet-stream")


@app.post("/ztp/complete")
def ztp_complete() -> tuple[Any, int]:
    auth_err = _check_proxy_auth()
    if auth_err is not None:
        return auth_err

    data = request.get_json(silent=True) or {}
    serial = (data.get("serial") or "").strip()
    hostname = (data.get("hostname") or "").strip()
    if not serial:
        return jsonify({"error": "missing_serial"}), 400

    LOG.info("ZTP completed successfully: serial=%s hostname=%s", serial, hostname)
    return {"status": "ok"}, 200


@app.get("/ztp/nvue-config/<serial>")
def ztp_nvue_config(serial: str) -> Response | tuple[Any, int]:
    auth_err = _check_proxy_auth()
    if auth_err is not None:
        return auth_err

    serial = (serial or "").strip()
    if not serial:
        return jsonify({"error": "missing_serial", "detail": "Path segment serial is required"}), 400

    hostip = request.remote_addr or "unknown"

    hostname = _lookup_hostname(serial)
    if not hostname:
        LOG.warning("No hostname mapped for serial=%s", serial)
        return jsonify({"error": "device_not_found", "serial": serial}), 404

    LOG.info("Resolved serial=%s to hostname=%s", serial, hostname)

    for ext, mimetype in ((".json", "application/json"), (".yaml", "text/yaml")):
        filepath = os.path.join(CONFIGS_DIR, f"{hostname}{ext}")
        if os.path.isfile(filepath):
            LOG.info("Serving config file: %s to %s at %s", filepath, serial, hostip)
            return send_file(filepath, mimetype=mimetype)

    LOG.warning("No config file found for hostname=%s (serial=%s)", hostname, serial)
    return jsonify({"error": "config_not_found", "hostname": hostname, "serial": serial}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
