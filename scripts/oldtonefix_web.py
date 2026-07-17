"""Local browser interface for oldtoneFix."""

import argparse
import json
import math
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.oldtonefix import (
    AFFTDN_NOISE_TYPES,
    DEFAULT_RNN_MODEL,
    PROGRESS_PREFIX,
    DenoiseTune,
)


WEB_ROOT = REPO_ROOT / "web"
DENOISE_SCRIPT = REPO_ROOT / "scripts" / "oldtonefix.py"
LOGO_PATH = REPO_ROOT / "assets" / "brand" / "oldtonefix-logo.png"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765
JOBS = {}
JOBS_LOCK = threading.Lock()

TUNE_FIELDS = {
    "highpass_hz": ("--highpass-hz", 1.0, 500.0),
    "rnnoise_mix": ("--rnnoise-mix", -1.0, 1.0),
    "afftdn_nr": ("--afftdn-nr", 0.01, 97.0),
    "afftdn_nf": ("--afftdn-nf", -80.0, -20.0),
    "treble_gain": ("--treble-gain", -20.0, 20.0),
    "treble_hz": ("--treble-hz", 1000.0, 16000.0),
    "treble_width": ("--treble-width", 0.01, 5.0),
}


def validate_job_payload(payload):
    """Return a normalized job payload or raise ValueError."""
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")

    input_path = payload.get("input")
    if not isinstance(input_path, str) or not input_path.strip():
        raise ValueError("Input path is required.")

    output_path = payload.get("output", "")
    if output_path is None:
        output_path = ""
    if not isinstance(output_path, str):
        raise ValueError("Output path must be text.")

    defaults = DenoiseTune()
    normalized = {
        "input": input_path.strip(),
        "output": output_path.strip(),
    }
    for boolean_name, default in (
        ("keep_existing", False),
        ("afftdn_tn", defaults.afftdn_tn),
    ):
        value = payload.get(boolean_name, default)
        if not isinstance(value, bool):
            raise ValueError(f"{boolean_name} must be true or false.")
        normalized[boolean_name] = value

    noise_type = payload.get("afftdn_nt", defaults.afftdn_nt)
    if noise_type not in AFFTDN_NOISE_TYPES:
        raise ValueError(
            f"afftdn_nt must be one of: {', '.join(AFFTDN_NOISE_TYPES)}."
        )
    normalized["afftdn_nt"] = noise_type

    for field, (_option, low, high) in TUNE_FIELDS.items():
        value = payload.get(field, getattr(defaults, field))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field} must be a number.")
        number = float(value)
        if not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f"{field} must be in [{low:g}, {high:g}].")
        normalized[field] = value

    return normalized


def _number_text(value):
    return f"{value:g}" if isinstance(value, float) else str(value)


def build_denoise_command(payload):
    """Build the existing CLI command for a validated browser request."""
    values = validate_job_payload(payload)
    command = [
        sys.executable,
        "-u",
        str(DENOISE_SCRIPT),
        "--input",
        values["input"],
        "--progress-json",
    ]
    if values["output"]:
        command.extend(("--output", values["output"]))
    if values["keep_existing"]:
        command.append("--keep-existing")

    for field, (option, _low, _high) in TUNE_FIELDS.items():
        command.extend((option, _number_text(values[field])))
    command.extend(("--afftdn-nt", values["afftdn_nt"]))
    command.append("--afftdn-tn" if values["afftdn_tn"] else "--no-afftdn-tn")
    return command


def detect_tools():
    ffmpeg = shutil.which("ffmpeg")
    model_exists = DEFAULT_RNN_MODEL.is_file()
    return {
        "ok": bool(ffmpeg and model_exists),
        "ffmpeg": bool(ffmpeg),
        "model": model_exists,
    }


def resolve_static_path(request_path, web_root=None):
    root = Path(web_root) if web_root is not None else WEB_ROOT
    path = unquote(urlsplit(request_path).path)
    relative = "index.html" if path == "/" else path.lstrip("/")
    candidate = root / relative
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def snapshot_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return None
        return {
            "id": job_id,
            "status": job["status"],
            "return_code": job["return_code"],
            "logs": list(job["logs"]),
            "command": list(job["command"]),
            "error": job["error"],
            "progress": dict(job["progress"]),
        }


def _finish_job(job_id, status, return_code=None, error=None):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        if job["stop_requested"]:
            status = "stopped"
        job["status"] = status
        job["return_code"] = return_code
        job["error"] = error
        job["process"] = None


def parse_progress_event(line):
    if not line.startswith(PROGRESS_PREFIX):
        return None
    try:
        event = json.loads(line.removeprefix(PROGRESS_PREFIX))
        completed = int(event["completed"])
        total = int(event["total"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if total < 0 or completed < 0 or completed > total:
        return None
    percent = round(completed * 100 / total) if total else 0
    return {
        "completed": completed,
        "total": total,
        "percent": percent,
        "current": str(event.get("current", "")),
        "status": str(event.get("status", "")),
    }


def run_job(job_id, command):
    """Run one CLI job and stream combined output into its in-memory log."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            return
        job["command"] = list(command)
        job["logs"].append(f"Running: {' '.join(command)}")

    try:
        process = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            env={
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as error:
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["logs"].append(f"Failed to start: {error}")
        _finish_job(job_id, "failed", return_code=1, error=str(error))
        return

    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["process"] = process

    assert process.stdout is not None
    for line in process.stdout:
        clean_line = line.rstrip("\r\n")
        if clean_line:
            progress = parse_progress_event(clean_line)
            with JOBS_LOCK:
                if job_id in JOBS:
                    if progress is None:
                        JOBS[job_id]["logs"].append(clean_line)
                    else:
                        JOBS[job_id]["progress"] = progress
    process.stdout.close()
    return_code = process.wait()
    status = "completed" if return_code == 0 else "failed"
    error = None if return_code == 0 else f"Process exited with code {return_code}."
    _finish_job(job_id, status, return_code=return_code, error=error)


def create_job(payload, start_thread=True):
    command = build_denoise_command(payload)
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running",
            "return_code": None,
            "logs": [],
            "command": command,
            "error": None,
            "process": None,
            "stop_requested": False,
            "progress": {
                "completed": 0,
                "total": 0,
                "percent": 0,
                "current": "",
                "status": "pending",
            },
        }
    if start_thread:
        threading.Thread(target=run_job, args=(job_id, command), daemon=True).start()
    return job_id


def stop_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            raise ValueError("Job not found.")
        job["stop_requested"] = True
        job["status"] = "stopped"
        process = job["process"]
    if process is not None and process.poll() is None:
        process.terminate()
    return job_id


class OldtoneFixWebHandler(BaseHTTPRequestHandler):
    server_version = "oldtoneFixWeb/1.0"

    def log_message(self, format, *args):
        return

    def write_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, path):
        if path is None or not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            self.write_json(200, detect_tools())
            return
        if path.startswith("/api/jobs/"):
            job = snapshot_job(path.rsplit("/", 1)[-1])
            if job is None:
                self.write_json(404, {"error": "Job not found."})
            else:
                self.write_json(200, job)
            return
        if path == "/logo.png":
            self.serve_file(LOGO_PATH)
            return
        self.serve_file(resolve_static_path(self.path))

    def do_POST(self):
        path = urlsplit(self.path).path
        if path.startswith("/api/jobs/") and path.endswith("/stop"):
            parts = path.strip("/").split("/")
            if len(parts) != 4:
                self.write_json(404, {"error": "Not found."})
                return
            try:
                stop_job(parts[2])
            except ValueError as error:
                self.write_json(404, {"error": str(error)})
                return
            self.write_json(202, {"job_id": parts[2], "status": "stopped"})
            return
        if path != "/api/jobs":
            self.write_json(404, {"error": "Not found."})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= 65536:
            self.write_json(400, {"error": "Invalid request body."})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            job_id = create_job(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            self.write_json(400, {"error": str(error)})
            return
        self.write_json(202, {"job_id": job_id})


def create_server(port):
    return ThreadingHTTPServer((HOST, port), OldtoneFixWebHandler)


def find_server(start_port=DEFAULT_PORT, attempts=20):
    for port in range(start_port, start_port + attempts):
        try:
            return create_server(port), port
        except OSError:
            continue
    raise RuntimeError(
        f"No available port found from {start_port} to {start_port + attempts - 1}."
    )


def create_parser():
    parser = argparse.ArgumentParser(description="Start the local oldtoneFix browser UI.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Preferred local port.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser automatically.")
    return parser


def main(argv=None):
    arguments = create_parser().parse_args(argv)
    try:
        server, port = find_server(arguments.port)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    url = f"http://{HOST}:{port}"
    print(f"oldtoneFix browser UI is running at {url}")
    print("Press Ctrl+C to stop.")
    if not arguments.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
