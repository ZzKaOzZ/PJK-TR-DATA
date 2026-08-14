# -*- coding: utf-8 -*-
"""PEA transformer field-collection server."""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, abort, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
LOCATIONS_FILE = DATA_DIR / "locations.json"
SUBMISSIONS_FILE = DATA_DIR / "submissions.json"
CONFIG_FILE = DATA_DIR / "config.json"
EXCEL_FILE = ROOT / "Rak-D.xlsx"
HTML_FILE = ROOT / "trpjk.html"

HOST = os.environ.get("PEA_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT") or os.environ.get("PEA_PORT", "5050"))
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif"}
MAX_CONTENT = 32 * 1024 * 1024

lock = threading.Lock()
PUBLIC_BASE = {"url": None}
def tunnel_key_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or str(DATA_DIR)
    folder = Path(base) / "pea-transformer"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


TUNNEL_KEY = None  # set on first use
TUNNEL_PUB = None

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default):
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_excel_locations() -> list[dict]:
    import openpyxl

    wb = openpyxl.load_workbook(EXCEL_FILE, data_only=True)
    ws = wb["WorkOrders"]
    rows = []
    for row in ws.iter_rows(min_row=3, values_only=True):
        rid, wo, addr, coords, meter, tr = row[0], row[1], row[2], row[3], row[4], row[5]
        if rid is None or not coords:
            continue
        lat_s, lng_s = str(coords).split(",")
        rows.append(
            {
                "id": int(rid),
                "wo": wo or "",
                "address": addr or "",
                "lat": float(lat_s.strip()),
                "lng": float(lng_s.strip()),
                "meter": meter or "",
                "transformer": tr or "",
                "assignee": row[14] or "",
                "queue": row[15] if row[15] is not None else "",
            }
        )
    return rows


def sync_locations() -> list[dict]:
    ensure_dirs()
    if EXCEL_FILE.exists():
        try:
            rows = load_excel_locations()
            write_json(LOCATIONS_FILE, rows)
            return rows
        except Exception as exc:
            print("Excel sync failed, using cache:", exc)
    cached = read_json(LOCATIONS_FILE, [])
    if not cached:
        raise RuntimeError("ไม่พบข้อมูลหม้อแปลง (Rak-D.xlsx หรือ data/locations.json)")
    return cached


def load_config() -> dict:
    ensure_dirs()
    cfg = read_json(CONFIG_FILE, {})
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(9).replace("_", "").replace("-", "")[:12]
        cfg["createdAt"] = utc_now()
        write_json(CONFIG_FILE, cfg)
    return cfg


def is_hosted() -> bool:
    return bool(
        os.environ.get("RENDER")
        or os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("FLY_APP_NAME")
        or os.environ.get("PUBLIC_URL")
    )


def hosted_public_url() -> str | None:
    cfg = load_config()
    path = f"/f/{cfg['token']}"
    env_url = (os.environ.get("PUBLIC_URL") or "").rstrip("/")
    if env_url:
        return env_url + path
    try:
        origin = request.host_url.rstrip("/")
        proto = request.headers.get("X-Forwarded-Proto")
        if proto:
            origin = proto.split(",")[0].strip() + "://" + request.host
        return origin + path
    except RuntimeError:
        return None


def lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


def feeder_complete(feeder: dict) -> bool:
    def filled(key: str) -> bool:
        val = feeder.get(key)
        return val is not None and str(val).strip() != ""

    return filled("ia") and filled("ib") and filled("ic")


def location_status(entry: dict | None) -> str:
    if not entry:
        return "pending"
    images = entry.get("images") or []
    feeders = entry.get("feeders") or []
    notes = (entry.get("notes") or "").strip()
    has_current = any(feeder_complete(f) for f in feeders)
    has_partial_current = any(
        str(f.get(k) or "").strip() for f in feeders for k in ("ia", "ib", "ic")
    )
    complete = len(images) > 0 and has_current
    if complete:
        return "completed"
    if images or notes or has_partial_current:
        return "partial"
    return "pending"


def submissions() -> dict:
    return read_json(SUBMISSIONS_FILE, {})


def save_submissions(data: dict) -> None:
    write_json(SUBMISSIONS_FILE, data)


def loc_key(loc_id) -> str:
    return str(loc_id)


def require_token() -> None:
    cfg = load_config()
    token = request.view_args.get("token") if request.view_args else None
    if not token:
        token = request.args.get("k") or request.headers.get("X-Share-Token")
    if token != cfg["token"]:
        abort(403, description="ลิงก์ไม่ถูกต้อง")


@app.route("/")
def root():
    cfg = load_config()
    return redirect(url_for("app_page", token=cfg["token"]))


@app.route("/f/<token>")
def app_page(token):
    cfg = load_config()
    if token != cfg["token"]:
        abort(403)
    return send_from_directory(ROOT, HTML_FILE.name)


def public_share_url() -> str | None:
    base = PUBLIC_BASE.get("url")
    if not base:
        return None
    cfg = load_config()
    return f"{base.rstrip('/')}/f/{cfg['token']}"


TUNNEL_ADMIN = "https://admin.localhost.run/"


def _tunnel_paths() -> tuple[Path, Path]:
    global TUNNEL_KEY, TUNNEL_PUB
    if TUNNEL_KEY is None:
        d = tunnel_key_dir()
        TUNNEL_KEY = d / "tunnel_key"
        TUNNEL_PUB = d / "tunnel_key.pub"
    return TUNNEL_KEY, TUNNEL_PUB
def ensure_tunnel_key() -> None:
    key, pub = _tunnel_paths()
    if key.exists():
        return
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key), "-N", "", "-q"],
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        os.chmod(key, 0o600)
    except OSError:
        pass


def tunnel_public_key() -> str:
    ensure_tunnel_key()
    _, pub = _tunnel_paths()
    return pub.read_text(encoding="utf-8").strip()


def mark_stable_tunnel(base_url: str) -> None:
    cfg = load_config()
    base = base_url.rstrip("/")
    if cfg.get("stableBaseUrl") == base:
        return
    cfg["stableBaseUrl"] = base
    cfg["tunnelKeyRegistered"] = True
    cfg["stableSince"] = utc_now()
    write_json(CONFIG_FILE, cfg)


def effective_public_url() -> str | None:
    live = public_share_url()
    if live:
        return live
    return None


TUNNEL_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.lhr\.life")


def load_saved_public_url() -> str | None:
    path = DATA_DIR / "public_url.txt"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def save_public_url(url: str | None) -> None:
    path = DATA_DIR / "public_url.txt"
    if url:
        path.write_text(url, encoding="utf-8")
    elif path.exists():
        path.unlink(missing_ok=True)


@app.before_request
def _honor_forwarded_proto():
    proto = request.headers.get("X-Forwarded-Proto")
    if proto:
        request.environ["wsgi.url_scheme"] = proto.split(",")[0].strip()


@app.route("/api/meta")
@app.route("/f/<token>/api/meta")
def api_meta(token=None):
    if token:
        require_token()
    cfg = load_config()
    ip = lan_ip()
    path = f"/f/{cfg['token']}"
    hosted = is_hosted()
    if hosted:
        display = hosted_public_url()
        return jsonify(
            {
                "token": cfg["token"],
                "localUrl": f"http://127.0.0.1:{PORT}{path}",
                "lanUrl": f"http://{ip}:{PORT}{path}",
                "publicUrl": display,
                "livePublicUrl": display,
                "stableBaseUrl": display,
                "tunnelReady": True,
                "tunnelRegistered": True,
                "tunnelPublicKey": "",
                "tunnelAdminUrl": "",
                "linkPermanent": True,
                "hosted": True,
                "tunnelNote": "ลิงก์นี้อยู่บนเซิร์ฟเวอร์ออนไลน์ ใช้ได้แม้ปิดคอมพิวเตอร์เครื่องนี้",
                "port": PORT,
            }
        )
    registered = bool(cfg.get("tunnelKeyRegistered"))
    stable = (cfg.get("stableBaseUrl") or "").rstrip("/")
    live = public_share_url()
    display = live
    live_matches = bool(live and stable and live.startswith(stable))
    note = "ลิงก์นี้ใช้ได้เฉพาะตอนเปิด start.bat ค้างไว้ ถ้าต้องการใช้ตอนปิดคอม ต้องขึ้นคลาวด์ตามไฟล์ วิธีขึ้นคลาวด์.txt"
    return jsonify(
        {
            "token": cfg["token"],
            "localUrl": f"http://127.0.0.1:{PORT}{path}",
            "lanUrl": f"http://{ip}:{PORT}{path}",
            "publicUrl": display,
            "livePublicUrl": live,
            "stableBaseUrl": stable or None,
            "tunnelReady": bool(PUBLIC_BASE.get("url")),
            "tunnelRegistered": registered,
            "tunnelPublicKey": "",
            "tunnelAdminUrl": TUNNEL_ADMIN,
            "linkPermanent": registered and live_matches,
            "hosted": False,
            "tunnelNote": note,
            "port": PORT,
        }
    )


@app.route("/api/tunnel/confirm", methods=["POST"])
@app.route("/f/<token>/api/tunnel/confirm", methods=["POST"])
def api_tunnel_confirm(token=None):
    if token:
        require_token()
    live = public_share_url()
    if not live:
        abort(400, description="ยังไม่มีลิงก์สาธารณะ รอสักครู่แล้วลองใหม่")
    return jsonify({
        "ok": True,
        "publicUrl": live,
        "restartRequired": False,
        "message": "คัดลอกลิงก์ล่าสุดในช่องด้านบน ส่งในไลน์ได้เลย",
    })


@app.route("/api/locations")
@app.route("/f/<token>/api/locations")
def api_locations(token=None):
    if token:
        require_token()
    with lock:
        locs = read_json(LOCATIONS_FILE, [])
        saved = submissions()
    out = []
    for loc in locs:
        entry = saved.get(loc_key(loc["id"]), {})
        out.append(
            {
                **loc,
                "data": {
                    "notes": entry.get("notes", ""),
                    "feeders": entry.get("feeders", []),
                    "images": entry.get("images", []),
                    "updatedAt": entry.get("updatedAt"),
                    "updatedBy": entry.get("updatedBy", ""),
                },
                "status": location_status(entry),
            }
        )
    return jsonify(out)


@app.route("/api/locations/<int:loc_id>", methods=["PUT"])
@app.route("/f/<token>/api/locations/<int:loc_id>", methods=["PUT"])
def api_save_location(loc_id, token=None):
    if token:
        require_token()
    payload = request.get_json(silent=True) or {}
    feeders = payload.get("feeders") or []
    cleaned = []
    for feeder in feeders:
        cleaned.append(
            {
                "name": str(feeder.get("name") or "").strip(),
                "ia": "" if feeder.get("ia") in (None, "") else str(feeder.get("ia")).strip(),
                "ib": "" if feeder.get("ib") in (None, "") else str(feeder.get("ib")).strip(),
                "ic": "" if feeder.get("ic") in (None, "") else str(feeder.get("ic")).strip(),
            }
        )
    with lock:
        locs = {int(x["id"]) for x in read_json(LOCATIONS_FILE, [])}
        if loc_id not in locs:
            abort(404)
        saved = submissions()
        key = loc_key(loc_id)
        current = saved.get(key, {})
        current["notes"] = str(payload.get("notes") or "")
        current["feeders"] = cleaned
        current["images"] = current.get("images") or []
        current["updatedAt"] = utc_now()
        current["updatedBy"] = str(payload.get("updatedBy") or "")[:80]
        saved[key] = current
        save_submissions(saved)
        status = location_status(current)
    return jsonify({"ok": True, "status": status, "data": current})


@app.route("/api/locations/<int:loc_id>/images", methods=["POST"])
@app.route("/f/<token>/api/locations/<int:loc_id>/images", methods=["POST"])
def api_upload_images(loc_id, token=None):
    if token:
        require_token()
    files = request.files.getlist("images")
    if not files:
        abort(400, description="ไม่พบไฟล์รูป")
    folder = UPLOAD_DIR / str(loc_id)
    folder.mkdir(parents=True, exist_ok=True)
    added = []
    with lock:
        locs = {int(x["id"]) for x in read_json(LOCATIONS_FILE, [])}
        if loc_id not in locs:
            abort(404)
        saved = submissions()
        key = loc_key(loc_id)
        current = saved.get(key, {"notes": "", "feeders": [], "images": []})
        current.setdefault("images", [])
        for f in files:
            ext = Path(f.filename or "").suffix.lower() or ".jpg"
            if ext not in ALLOWED_IMAGE_EXT:
                ext = ".jpg"
            name = f"{uuid.uuid4().hex}{ext}"
            dest = folder / name
            f.save(dest)
            rec = {"filename": name, "original": Path(f.filename or name).name}
            current["images"].append(rec)
            added.append(rec)
        current["updatedAt"] = utc_now()
        saved[key] = current
        save_submissions(saved)
        status = location_status(current)
    return jsonify({"ok": True, "images": added, "status": status})


@app.route("/api/locations/<int:loc_id>/images/<filename>", methods=["DELETE"])
@app.route("/f/<token>/api/locations/<int:loc_id>/images/<filename>", methods=["DELETE"])
def api_delete_image(loc_id, filename, token=None):
    if token:
        require_token()
    if "/" in filename or "\\" in filename:
        abort(400)
    with lock:
        saved = submissions()
        key = loc_key(loc_id)
        current = saved.get(key)
        if not current:
            abort(404)
        before = len(current.get("images") or [])
        current["images"] = [img for img in current.get("images") or [] if img.get("filename") != filename]
        if len(current["images"]) == before:
            abort(404)
        current["updatedAt"] = utc_now()
        saved[key] = current
        save_submissions(saved)
        path = UPLOAD_DIR / str(loc_id) / filename
        if path.exists():
            path.unlink()
        status = location_status(current)
    return jsonify({"ok": True, "status": status})


@app.route("/uploads/<int:loc_id>/<path:filename>")
def serve_upload(loc_id, filename):
    folder = UPLOAD_DIR / str(loc_id)
    return send_from_directory(folder, filename)


def _open_shared_log(path: Path):
    """Write handle that still allows another open() to read on Windows."""
    if os.name != "nt":
        return path.open("wb")
    import ctypes
    import msvcrt

    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    CREATE_ALWAYS = 2
    FILE_ATTRIBUTE_NORMAL = 0x80
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(
        str(path),
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        CREATE_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise OSError("Cannot create shared tunnel log")
    fd = msvcrt.open_osfhandle(handle, os.O_APPEND)
    return os.fdopen(fd, "wb", buffering=0)


def start_public_tunnel() -> None:
    ensure_tunnel_key()
    key, _ = _tunnel_paths()
    log_path = Path(os.environ.get("TEMP", str(DATA_DIR))) / "pea-tunnel.log"

    def build_cmd(use_key: bool) -> list[str]:
        host = "localhost.run" if use_key else "nokey@localhost.run"
        cmd = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=NUL",
               "-o", "GlobalKnownHostsFile=NUL", "-o", "ServerAliveInterval=30",
               "-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes", "-T",
               "-R", f"80:127.0.0.1:{PORT}", host]
        if use_key:
            cmd[1:1] = ["-i", str(key), "-o", "IdentitiesOnly=yes"]
        return cmd

    def remember(url: str) -> None:
        PUBLIC_BASE["url"] = url.rstrip("/")
        share = public_share_url()
        print("PUBLIC_URL=" + (share or ""))
        save_public_url(share)
        cfg = load_config()
        stable = (cfg.get("stableBaseUrl") or "").rstrip("/")
        if cfg.get("tunnelKeyRegistered") and stable and url.rstrip("/") == stable:
            print("  (ลิงก์ถาวรตรงกับที่ลงทะเบียน)")
        else:
            print("  ส่งลิงก์นี้ให้ทีม — ลิงก์เก่าใช้ไม่ได้")

    def run() -> None:
        force_nokey = False
        while True:
            cfg = load_config()
            use_key = bool(cfg.get("tunnelKeyRegistered")) and not force_nokey
            mode = "SSH key" if use_key else "ชั่วคราว (nokey)"
            print(f"Creating public HTTPS link ({mode})...")
            PUBLIC_BASE["url"] = None
            cmd = build_cmd(use_key)
            try:
                log = _open_shared_log(log_path)
                proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
                seen = None
                while proc.poll() is None:
                    try:
                        log.flush()
                    except OSError:
                        pass
                    try:
                        text = subprocess.check_output(
                            [
                                "python",
                                "-c",
                                "import sys; sys.stdout.buffer.write(open(sys.argv[1], 'rb').read())",
                                str(log_path),
                            ],
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        ).decode("utf-8", errors="replace")
                    except Exception as exc:
                        print("tunnel log read retry:", exc)
                        time.sleep(0.4)
                        continue
                    match = TUNNEL_URL_RE.search(text)
                    if match:
                        url = match.group(0).rstrip("/")
                        if url != seen:
                            seen = url
                            remember(url)
                    time.sleep(0.5)
                code = proc.wait()
                log.close()
                print("ssh tunnel exited:", code)
                try:
                    log_text = log_path.read_bytes().decode("utf-8", errors="replace")
                except OSError:
                    log_text = ""
                if use_key and ("Permission denied" in log_text or "publickey" in log_text):
                    print("SSH key ยังใช้ไม่ได้ — สลับไปลิงก์ชั่วคราว")
                    force_nokey = True
                    cfg = load_config()
                    cfg["tunnelKeyRegistered"] = False
                    write_json(CONFIG_FILE, cfg)
            except Exception as exc:
                print("Public tunnel error:", exc)
            PUBLIC_BASE["url"] = None
            save_public_url(None)
            print("Public link dropped, reconnecting in 3s...")
            time.sleep(3)

    threading.Thread(target=run, daemon=True).start()


def print_banner() -> None:
    cfg = load_config()
    ip = lan_ip()
    path = f"/f/{cfg['token']}"
    print()
    print("=" * 60)
    print("  PEA Data Collection  |  ระบบเก็บข้อมูลหม้อแปลง")
    print("=" * 60)
    print(f"  เปิดบนเครื่องนี้ : http://127.0.0.1:{PORT}{path}")
    print(f"  ลิงก์ใน Wi-Fi    : http://{ip}:{PORT}{path}")
    print("  ลิงก์ชั่วคราวใช้ได้เฉพาะตอนเปิด start.bat ค้างไว้")
    print("  ถ้าต้องการใช้ตอนปิดคอม ให้อ่านไฟล์ วิธีขึ้นคลาวด์.txt")
    print("=" * 60)
    print()


ensure_dirs()
load_config()
try:
    print(f"Loaded {len(sync_locations())} transformer locations")
except Exception as exc:
    print("Location load warning:", exc)

if __name__ == "__main__":
    print_banner()
    if is_hosted():
        print("Cloud host detected — skipping local tunnel")
    else:
        start_public_tunnel()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
