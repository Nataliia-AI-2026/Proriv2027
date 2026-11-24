"""Міні-застосунок: маленький вебсервер для сторінки «Послуги, запис і контакти» всередині Telegram.
Працює поруч з агентом у тому ж Railway-сервісі. Адресу дає Railway (Settings → Networking → Generate Domain),
агент бере її сам зі змінної RAILWAY_PUBLIC_DOMAIN (або WEBAPP_URL, якщо задати вручну).
Сторінка: webapp/index.html. Тексти й ціни: texts.py (APP, APP_CARDS, APP_LINKS), віддаються як /data.json."""
import hashlib
import hmac
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

import config

log = logging.getLogger("webapp")
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "webapp", "index.html")
ADVENT_PAGE = os.path.join(HERE, "webapp", "advent.html")   # адвент-календар (advent.py)
IMG_DIR = os.path.join(HERE, "images")
IMG_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def app_url():
    """Адреса сторінки або "" (тоді в меню старі кнопки)."""
    url = os.environ.get("WEBAPP_URL", "").strip()
    if not url and os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip():
        url = "https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"].strip() + "/"
    return url if url.startswith("https://") else ""


def user_from_init_data(init_data):
    """Перевіряє підпис Telegram і повертає id людини або 0."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        got = pairs.pop("hash", "")
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
        want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if got and hmac.compare_digest(got, want):
            return int(json.loads(pairs.get("user", "{}")).get("id") or 0)
    except Exception:
        pass
    return 0


def start(get_data, on_open, advent_api=None):
    """get_data() → словник для сторінки; on_open(uid) → записати, що людина відкрила застосунок;
    advent_api(path, body) → (код, словник) для запитів сторінки адвент-календаря."""
    port = int(os.environ.get("PORT", "8080"))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype, cache="no-cache"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    with open(PAGE, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                if path in ("/advent", "/advent/") and os.path.isfile(ADVENT_PAGE):
                    with open(ADVENT_PAGE, "rb") as f:
                        return self._send(200, f.read(), "text/html; charset=utf-8")
                if path == "/data.json":
                    body = json.dumps(get_data(), ensure_ascii=False).encode()
                    return self._send(200, body, "application/json; charset=utf-8")
                if path.startswith("/images/"):
                    name = os.path.basename(path)
                    ext = os.path.splitext(name)[1].lower()
                    full = os.path.join(IMG_DIR, name)
                    if ext in IMG_TYPES and os.path.isfile(full):
                        with open(full, "rb") as f:
                            return self._send(200, f.read(), IMG_TYPES[ext], "public, max-age=3600")
                self._send(404, b"not found", "text/plain")
            except Exception as e:
                log.warning("webapp GET %s: %s", path, e)
                self._send(500, b"error", "text/plain")

        def do_POST(self):
            path = urlparse(self.path).path
            if path.startswith("/advent/") and advent_api:
                try:
                    n = min(int(self.headers.get("Content-Length") or 0), 65536)
                    code, data = advent_api(path, self.rfile.read(n).decode("utf-8", "replace"))
                except Exception as e:
                    log.warning("advent %s: %s", path, e)
                    code, data = 500, {"ok": False}
                return self._send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            if path != "/open":
                return self._send(404, b"not found", "text/plain")
            try:
                n = min(int(self.headers.get("Content-Length") or 0), 8192)
                uid = user_from_init_data(self.rfile.read(n).decode())
                if uid:
                    on_open(uid)
            except Exception as e:
                log.warning("webapp open: %s", e)
            self._send(204, b"", "text/plain")

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError as e:
        log.warning("Міні-застосунок не запустився (порт %s): %s", port, e)
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Міні-застосунок слухає порт %s, адреса: %s", port, app_url() or "ще не створена в Railway")
