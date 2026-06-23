#!/usr/bin/env python3
"""Tiny Zettai Agent API preview server.

This intentionally uses only the Python standard library so it can run on the
Lanxin board without first installing a web framework. It is an MVP boundary:
OAuth/session/token/metering are real, while the Sandlock executor is a stub
that can be swapped for the RISC-V runtime worker.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def now() -> int:
    return int(time.time())


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Config:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.db_path = Path(os.getenv("ZETTAI_DB", "./zettai_agent_api.sqlite3"))
        self.static_root = Path(os.getenv("ZETTAI_STATIC_ROOT", ".")).resolve()
        self.public_url = os.getenv("ZETTAI_PUBLIC_URL", f"http://127.0.0.1:{port}").rstrip("/")
        self.frontend_url = os.getenv("ZETTAI_FRONTEND_URL", f"{self.public_url}/agent-api/").rstrip("/")
        self.cookie_name = os.getenv("ZETTAI_COOKIE_NAME", "zettai_session")
        self.cookie_domain = os.getenv("ZETTAI_COOKIE_DOMAIN", "")
        self.dev_login = os.getenv("ZETTAI_DEV_LOGIN", "1") == "1"
        self.dev_pack_tokens = env_int("ZETTAI_DEV_PACK_TOKENS", 100000)
        self.run_token_cost = env_int("ZETTAI_RUN_TOKEN_COST", 10)
        self.github_client_id = os.getenv("GITHUB_CLIENT_ID", "")
        self.github_client_secret = os.getenv("GITHUB_CLIENT_SECRET", "")
        self.google_client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        self.google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
        self.stripe_secret_key = os.getenv("STRIPE_SECRET_KEY", "")
        self.stripe_price_starter = os.getenv("STRIPE_PRICE_STARTER", "")
        self.stripe_success_url = os.getenv("STRIPE_SUCCESS_URL", f"{self.frontend_url}?checkout=success")
        self.stripe_cancel_url = os.getenv("STRIPE_CANCEL_URL", f"{self.frontend_url}?checkout=cancel")

    def callback_url(self, provider: str) -> str:
        return f"{self.public_url}/auth/{provider}/callback"


CFG: Config


SCHEMA = (
    """
    create table if not exists users (
        id integer primary key autoincrement,
        provider text not null,
        external_id text not null,
        email text not null,
        name text not null default '',
        token_balance integer not null default 0,
        created_at integer not null,
        unique(provider, external_id)
    )
    """,
    """
    create table if not exists sessions (
        token_hash text primary key,
        user_id integer not null,
        expires_at integer not null,
        created_at integer not null
    )
    """,
    """
    create table if not exists api_tokens (
        id integer primary key autoincrement,
        token_hash text not null unique,
        prefix text not null,
        user_id integer not null,
        name text not null,
        created_at integer not null,
        revoked_at integer
    )
    """,
    """
    create table if not exists usage_events (
        id integer primary key autoincrement,
        user_id integer not null,
        api_token_id integer not null,
        endpoint text not null,
        tokens_used integer not null,
        request_json text not null,
        created_at integer not null
    )
    """,
    """
    create table if not exists oauth_states (
        state text primary key,
        provider text not null,
        created_at integer not null
    )
    """,
    """
    create table if not exists purchases (
        id integer primary key autoincrement,
        user_id integer not null,
        provider text not null,
        external_id text not null,
        tokens integer not null,
        created_at integer not null
    )
    """,
)


def connect() -> sqlite3.Connection:
    CFG.db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CFG.db_path)
    con.row_factory = sqlite3.Row
    con.execute("pragma journal_mode=wal")
    con.execute("pragma busy_timeout=5000")
    return con


def init_db() -> None:
    with connect() as con:
        for stmt in SCHEMA:
            con.execute(stmt)


def upsert_user(provider: str, external_id: str, email: str, name: str) -> sqlite3.Row:
    with connect() as con:
        row = con.execute(
            "select * from users where provider=? and external_id=?",
            (provider, external_id),
        ).fetchone()
        if row:
            con.execute(
                "update users set email=?, name=? where id=?",
                (email, name, row["id"]),
            )
            return con.execute("select * from users where id=?", (row["id"],)).fetchone()
        cur = con.execute(
            """
            insert into users(provider, external_id, email, name, token_balance, created_at)
            values(?, ?, ?, ?, ?, ?)
            """,
            (provider, external_id, email, name, 1000, now()),
        )
        return con.execute("select * from users where id=?", (cur.lastrowid,)).fetchone()


def create_session(user_id: int) -> str:
    token = "zs_" + secrets.token_urlsafe(32)
    expires = now() + 30 * 24 * 60 * 60
    with connect() as con:
        con.execute(
            "insert into sessions(token_hash, user_id, expires_at, created_at) values(?, ?, ?, ?)",
            (sha256_hex(token), user_id, expires, now()),
        )
    return token


def request_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(min(length, 2 * 1024 * 1024))
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON body") from exc
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def http_json(url: str, data: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = None
    req_headers = {"Accept": "application/json", **(headers or {})}
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=body, headers=req_headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def bearer_auth(handler: BaseHTTPRequestHandler) -> tuple[sqlite3.Row, sqlite3.Row]:
    auth = handler.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise PermissionError("missing bearer token")
    raw_token = auth.removeprefix("Bearer ").strip()
    with connect() as con:
        token = con.execute(
            "select * from api_tokens where token_hash=? and revoked_at is null",
            (sha256_hex(raw_token),),
        ).fetchone()
        if not token:
            raise PermissionError("invalid bearer token")
        user = con.execute("select * from users where id=?", (token["user_id"],)).fetchone()
        if not user:
            raise PermissionError("token has no user")
        return user, token


class Handler(BaseHTTPRequestHandler):
    server_version = "ZettaiAgentAPI/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.getenv("ZETTAI_VERBOSE", "0") == "1":
            super().log_message(fmt, *args)

    def end_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def send_json(self, code: int, value: Any, extra_headers: dict[str, str] | None = None) -> None:
        raw = json_bytes(value)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for key, val in (extra_headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(raw)

    def redirect(self, url: str, extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", url)
        for key, val in (extra_headers or {}).items():
            self.send_header(key, val)
        self.end_headers()

    def user_from_cookie(self) -> sqlite3.Row | None:
        raw_cookie = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie()
        jar.load(raw_cookie)
        morsel = jar.get(CFG.cookie_name)
        if not morsel:
            return None
        token_hash = sha256_hex(morsel.value)
        with connect() as con:
            sess = con.execute(
                "select * from sessions where token_hash=? and expires_at>?",
                (token_hash, now()),
            ).fetchone()
            if not sess:
                return None
            return con.execute("select * from users where id=?", (sess["user_id"],)).fetchone()

    def require_user(self) -> sqlite3.Row:
        user = self.user_from_cookie()
        if not user:
            raise PermissionError("login required")
        return user

    def session_cookie(self, token: str) -> str:
        parts = [
            f"{CFG.cookie_name}={token}",
            "Path=/",
            "Max-Age=2592000",
            "HttpOnly",
            "SameSite=Lax",
        ]
        if CFG.cookie_domain:
            parts.append(f"Domain={CFG.cookie_domain}")
        if CFG.public_url.startswith("https://"):
            parts.append("Secure")
        return "; ".join(parts)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/health":
                self.send_json(200, {"ok": True, "db": str(CFG.db_path), "static_root": str(CFG.static_root)})
            elif path == "/api/me":
                self.handle_me()
            elif path == "/api/tokens":
                self.handle_list_tokens()
            elif path == "/auth/github/start":
                self.handle_oauth_start("github")
            elif path == "/auth/google/start":
                self.handle_oauth_start("google")
            elif path == "/auth/github/callback":
                self.handle_github_callback(parsed)
            elif path == "/auth/google/callback":
                self.handle_google_callback(parsed)
            else:
                self.serve_static(path)
        except PermissionError as exc:
            self.send_json(401, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/dev-login":
                self.handle_dev_login()
            elif path == "/api/tokens":
                self.handle_create_token()
            elif path == "/api/checkout":
                self.handle_checkout()
            elif path == "/v1/agent/run":
                self.handle_agent_run()
            else:
                self.send_json(404, {"error": "not found"})
        except PermissionError as exc:
            self.send_json(401, {"error": str(exc)})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def handle_me(self) -> None:
        user = self.require_user()
        with connect() as con:
            usage = con.execute(
                "select coalesce(sum(tokens_used), 0) as total_tokens_used from usage_events where user_id=?",
                (user["id"],),
            ).fetchone()
        self.send_json(
            200,
            {
                "user": {
                    "id": user["id"],
                    "provider": user["provider"],
                    "external_id": user["external_id"],
                    "email": user["email"],
                    "name": user["name"],
                    "token_balance": user["token_balance"],
                },
                "usage": {"total_tokens_used": usage["total_tokens_used"]},
            },
        )

    def handle_dev_login(self) -> None:
        if not CFG.dev_login:
            raise PermissionError("dev login disabled")
        data = request_json(self)
        email = str(data.get("email") or "founder@zett.ai")
        name = str(data.get("name") or "Zettai Founder")
        user = upsert_user("dev", email, email, name)
        token = create_session(user["id"])
        self.send_json(200, {"ok": True, "user_id": user["id"]}, {"Set-Cookie": self.session_cookie(token)})

    def handle_list_tokens(self) -> None:
        user = self.require_user()
        with connect() as con:
            rows = con.execute(
                "select id, prefix, name, created_at, revoked_at from api_tokens where user_id=? order by id desc",
                (user["id"],),
            ).fetchall()
        self.send_json(200, {"tokens": [dict(row) for row in rows]})

    def handle_create_token(self) -> None:
        user = self.require_user()
        data = request_json(self)
        name = str(data.get("name") or "default")[:80]
        raw = "ztai_" + secrets.token_urlsafe(32)
        prefix = raw[:14]
        with connect() as con:
            con.execute(
                "insert into api_tokens(token_hash, prefix, user_id, name, created_at) values(?, ?, ?, ?, ?)",
                (sha256_hex(raw), prefix, user["id"], name, now()),
            )
        self.send_json(201, {"token": raw, "prefix": prefix, "name": name})

    def handle_checkout(self) -> None:
        user = self.require_user()
        if CFG.stripe_secret_key and CFG.stripe_price_starter:
            payload = {
                "mode": "payment",
                "line_items[0][price]": CFG.stripe_price_starter,
                "line_items[0][quantity]": "1",
                "success_url": CFG.stripe_success_url,
                "cancel_url": CFG.stripe_cancel_url,
                "client_reference_id": str(user["id"]),
                "metadata[user_id]": str(user["id"]),
            }
            resp = http_json(
                "https://api.stripe.com/v1/checkout/sessions",
                payload,
                {"Authorization": f"Bearer {CFG.stripe_secret_key}"},
            )
            self.send_json(200, {"url": resp.get("url"), "id": resp.get("id")})
            return
        with connect() as con:
            con.execute(
                "update users set token_balance=token_balance+? where id=?",
                (CFG.dev_pack_tokens, user["id"]),
            )
            con.execute(
                "insert into purchases(user_id, provider, external_id, tokens, created_at) values(?, ?, ?, ?, ?)",
                (user["id"], "dev", "checkout-stub", CFG.dev_pack_tokens, now()),
            )
        self.send_json(
            200,
            {
                "message": f"Stripe is not configured; added {CFG.dev_pack_tokens} preview tokens.",
                "tokens_added": CFG.dev_pack_tokens,
            },
        )

    def handle_agent_run(self) -> None:
        data = request_json(self)
        user, token = bearer_auth(self)
        charge = max(1, CFG.run_token_cost)
        request_snapshot = json.dumps(data, separators=(",", ":"), ensure_ascii=False)[:8192]
        with connect() as con:
            con.execute("begin immediate")
            fresh = con.execute("select token_balance from users where id=?", (user["id"],)).fetchone()
            if fresh["token_balance"] < charge:
                con.execute("rollback")
                raise PermissionError("insufficient token balance")
            con.execute("update users set token_balance=token_balance-? where id=?", (charge, user["id"]))
            con.execute(
                """
                insert into usage_events(user_id, api_token_id, endpoint, tokens_used, request_json, created_at)
                values(?, ?, ?, ?, ?, ?)
                """,
                (user["id"], token["id"], "/v1/agent/run", charge, request_snapshot, now()),
            )
            con.execute("commit")
        run_id = "run_" + secrets.token_hex(8)
        self.send_json(
            200,
            {
                "id": run_id,
                "status": "queued",
                "runtime": "sandlock-riscv-preview",
                "tokens_charged": charge,
                "message": "Agent run accepted. The RISC-V Sandlock executor can be attached behind this endpoint.",
                "request": {
                    "image": data.get("image", ""),
                    "command": data.get("command", ""),
                    "network": data.get("network", "default-deny"),
                },
            },
        )

    def handle_oauth_start(self, provider: str) -> None:
        if provider == "github":
            if not (CFG.github_client_id and CFG.github_client_secret):
                self.send_json(503, {"error": "GitHub OAuth is not configured"})
                return
            base = "https://github.com/login/oauth/authorize"
            params = {
                "client_id": CFG.github_client_id,
                "redirect_uri": CFG.callback_url("github"),
                "scope": "read:user user:email",
                "state": self.create_oauth_state(provider),
            }
        elif provider == "google":
            if not (CFG.google_client_id and CFG.google_client_secret):
                self.send_json(503, {"error": "Google OAuth is not configured"})
                return
            base = "https://accounts.google.com/o/oauth2/v2/auth"
            params = {
                "client_id": CFG.google_client_id,
                "redirect_uri": CFG.callback_url("google"),
                "response_type": "code",
                "scope": "openid email profile",
                "state": self.create_oauth_state(provider),
                "access_type": "online",
            }
        else:
            self.send_json(404, {"error": "unknown provider"})
            return
        self.redirect(base + "?" + urllib.parse.urlencode(params))

    def create_oauth_state(self, provider: str) -> str:
        state = secrets.token_urlsafe(24)
        with connect() as con:
            con.execute(
                "insert into oauth_states(state, provider, created_at) values(?, ?, ?)",
                (state, provider, now()),
            )
        return state

    def consume_oauth_state(self, provider: str, state: str) -> None:
        with connect() as con:
            row = con.execute("select * from oauth_states where state=? and provider=?", (state, provider)).fetchone()
            if not row or row["created_at"] < now() - 600:
                raise PermissionError("invalid OAuth state")
            con.execute("delete from oauth_states where state=?", (state,))

    def finish_login(self, provider: str, external_id: str, email: str, name: str) -> None:
        user = upsert_user(provider, external_id, email, name)
        token = create_session(user["id"])
        self.redirect(CFG.frontend_url, {"Set-Cookie": self.session_cookie(token)})

    def handle_github_callback(self, parsed: urllib.parse.ParseResult) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        code = query.get("code", [""])[0]
        state = query.get("state", [""])[0]
        self.consume_oauth_state("github", state)
        token_resp = http_json(
            "https://github.com/login/oauth/access_token",
            {
                "client_id": CFG.github_client_id,
                "client_secret": CFG.github_client_secret,
                "code": code,
                "redirect_uri": CFG.callback_url("github"),
            },
        )
        access_token = token_resp.get("access_token")
        if not access_token:
            raise PermissionError("GitHub token exchange failed")
        headers = {"Authorization": f"Bearer {access_token}", "User-Agent": "zettai-agent-api"}
        profile = http_json("https://api.github.com/user", headers=headers)
        email = profile.get("email") or ""
        if not email:
            emails = http_json("https://api.github.com/user/emails", headers=headers)
            for item in emails if isinstance(emails, list) else []:
                if item.get("primary") and item.get("verified"):
                    email = item.get("email", "")
                    break
        self.finish_login("github", str(profile.get("id")), email or f"github-{profile.get('id')}@users.noreply.github.com", profile.get("name") or profile.get("login") or "")

    def handle_google_callback(self, parsed: urllib.parse.ParseResult) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        code = query.get("code", [""])[0]
        state = query.get("state", [""])[0]
        self.consume_oauth_state("google", state)
        token_resp = http_json(
            "https://oauth2.googleapis.com/token",
            {
                "client_id": CFG.google_client_id,
                "client_secret": CFG.google_client_secret,
                "code": code,
                "redirect_uri": CFG.callback_url("google"),
                "grant_type": "authorization_code",
            },
        )
        access_token = token_resp.get("access_token")
        if not access_token:
            raise PermissionError("Google token exchange failed")
        profile = http_json("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {access_token}"})
        self.finish_login("google", str(profile.get("sub")), profile.get("email") or "", profile.get("name") or "")

    def serve_static(self, url_path: str) -> None:
        rel = urllib.parse.unquote(url_path).lstrip("/")
        if not rel:
            rel = "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        target = (CFG.static_root / rel).resolve()
        try:
            target.relative_to(CFG.static_root)
        except ValueError:
            self.send_json(403, {"error": "forbidden"})
            return
        if not target.exists() or not target.is_file():
            self.send_json(404, {"error": "not found"})
            return
        raw = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix in {".html", ".js", ".css", ".svg"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=env_int("PORT", 8787))
    args = parser.parse_args()
    global CFG
    CFG = Config(args.host, args.port)
    init_db()
    print(f"Zettai Agent API listening on http://{args.host}:{args.port}")
    print(f"static_root={CFG.static_root}")
    print(f"db={CFG.db_path}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
