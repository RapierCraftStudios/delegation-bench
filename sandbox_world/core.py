"""Sandbox world core: per-run tenants, host routing, banner/noindex layer and `/__state`.

Standard library only. This module opens no client sockets: the sandbox never talks to the
outside world, mail is an in-memory stub and every site is a pure function of tenant state.
"""

from __future__ import annotations

import hmac
import html as _html
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import default as _default_policy
from http.cookies import SimpleCookie
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

DEMO_RUN_PREFIX = "demo_"
RUN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
HOST_SUFFIXES = (".sandbox.localhost", ".sandbox.alterlab.io", ".localhost")
BANNER = (
    '<div id="sandbox-banner" role="note" style="background:#fde68a;color:#78350f;padding:6px 12px;'
    'font:600 13px sans-serif;border-bottom:2px solid #f59e0b">SANDBOX &mdash; fictional test site. '
    "No real data, nothing here is real.</div>"
)
ROBOTS_META = '<meta name="robots" content="noindex, nofollow, noarchive">'
ROBOTS_HEADER = "noindex, nofollow, noarchive"
STATE_TOKEN_HEADER = "x-sandbox-state-token"

esc = _html.escape

# site name -> {"title": str, "initial": callable, "router": Router}
SITES: dict[str, dict[str, Any]] = {}


@dataclass
class Response:
    status: int = 200
    body: Any = b""
    kind: str = "html"  # html | json | raw
    ctype: str = "text/html; charset=utf-8"
    title: str = ""
    headers: dict[str, str] = field(default_factory=dict)


def html(body: str, title: str = "", status: int = 200) -> Response:
    return Response(status=status, body=body, title=title)


def js(obj: Any, status: int = 200) -> Response:
    return Response(status=status, body=obj, kind="json", ctype="application/json")


def raw(data: bytes, ctype: str, headers: dict[str, str] | None = None) -> Response:
    return Response(body=data, kind="raw", ctype=ctype, headers=headers or {})


def redirect(location: str, status: int = 303) -> Response:
    return Response(
        status=status, kind="raw", ctype="text/plain", body=b"", headers={"Location": location}
    )


@dataclass
class Ctx:
    method: str
    path: str
    query: dict[str, str]
    form: dict[str, str]
    files: list[dict[str, Any]]
    headers: dict[str, str]
    json_body: Any
    site: str
    tenant: "Tenant"
    prefix: str
    clock: Callable[[], float]

    @property
    def state(self) -> dict[str, Any]:
        return self.tenant.state(self.site)

    def url(self, path: str) -> str:
        return f"{self.prefix}{path}"

    @property
    def demo(self) -> bool:
        """True for a public-demo tenant (run ids ``demo_*``, #39832).

        Demo-only traps (a booking that loses a race, a cancel page that does not cancel, a ticket
        filed against the wrong order) fire only here, so benchmark runs (T01-T25) and the hands-e2e
        stack keep their documented behaviour.
        """
        return self.tenant.run.startswith(DEMO_RUN_PREFIX)

    def now(self) -> float:
        return self.clock()

    def deliver(
        self, to: str, subject: str, body: str, sender: str = "noreply@sandbox.invalid"
    ) -> None:
        """Drop a message into the run's mailbox stub (the only 'email' there is)."""
        inbox = self.tenant.state("mail")["inbox"]
        inbox.append(
            {"id": len(inbox) + 1, "to": to, "from": sender, "subject": subject, "body": body}
        )


class Router:
    def __init__(self) -> None:
        self.routes: list[tuple[str, re.Pattern[str], Callable[..., Response]]] = []

    def route(self, method: str, pattern: str):
        rx = re.compile("^" + re.sub(r"<(\w+)>", r"(?P<\1>[^/]+)", pattern) + "$")

        def deco(fn: Callable[..., Response]):
            self.routes.append((method, rx, fn))
            return fn

        return deco

    def dispatch(self, ctx: Ctx) -> Response:
        allowed = False
        for method, rx, fn in self.routes:
            m = rx.match(ctx.path)
            if m:
                if method == ctx.method:
                    return fn(ctx, **m.groupdict())
                allowed = True
        return html(
            "<h1>%s</h1>" % ("Method not allowed" if allowed else "Not found"),
            "Error",
            405 if allowed else 404,
        )


def register(name: str, title: str, initial: Callable[[], dict[str, Any]]) -> Router:
    router = Router()
    SITES[name] = {"title": title, "initial": initial, "router": router}
    return router


class Tenant:
    def __init__(self, run: str, now: float) -> None:
        self.run = run
        self.touched = now
        self._sites: dict[str, dict[str, Any]] = {}

    def state(self, site: str) -> dict[str, Any]:
        if site not in self._sites:
            self._sites[site] = SITES[site]["initial"]()
        return self._sites[site]


class Store:
    """Per-run tenants. A run id selects an isolated world; idle runs reset themselves."""

    def __init__(
        self, clock: Callable[[], float] = time.time, ttl: float = 900.0, max_tenants: int = 500
    ) -> None:
        self.clock, self.ttl, self.max_tenants = clock, ttl, max_tenants
        self._tenants: dict[str, Tenant] = {}
        self._lock = threading.RLock()

    def get(self, run: str) -> Tenant:
        with self._lock:
            now = self.clock()
            tenant = self._tenants.get(run)
            if tenant is not None and now - tenant.touched > self.ttl:
                tenant = None  # idle reset: the next request sees a fresh world
            if tenant is None:
                if len(self._tenants) >= self.max_tenants:
                    oldest = min(self._tenants.values(), key=lambda t: t.touched)
                    del self._tenants[oldest.run]
                tenant = self._tenants[run] = Tenant(run, now)
            tenant.touched = now
            return tenant

    def reset(self, run: str) -> None:
        with self._lock:
            self._tenants.pop(run, None)


def parse_multipart(ctype: str, body: bytes) -> tuple[dict[str, str], list[dict[str, Any]]]:
    import hashlib

    msg = BytesParser(policy=_default_policy).parsebytes(
        b"MIME-Version: 1.0\r\nContent-Type: " + ctype.encode() + b"\r\n\r\n" + body
    )
    fields: dict[str, str] = {}
    files: list[dict[str, Any]] = []
    for part in msg.iter_parts() if msg.is_multipart() else []:
        name = part.get_param("name", header="content-disposition") or ""
        data = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is None:
            fields[name] = data.decode("utf-8", "replace")
        else:
            files.append(
                {
                    "field": name,
                    "filename": filename,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "is_pdf": data.startswith(b"%PDF"),
                }
            )
    return fields, files


class App:
    def __init__(
        self, store: Store | None = None, public: bool = False, state_token: str = ""
    ) -> None:
        self.store = store or Store()
        self.public = public
        self.state_token = state_token

    # -- request plumbing -------------------------------------------------------------------
    def _resolve_site(self, host: str, path: str) -> tuple[str | None, str, str]:
        label = host.split(":")[0].lower()
        if label.endswith(HOST_SUFFIXES):
            first = label.split(".")[0]
            if first in SITES:
                return first, path, ""
        m = re.match(r"^/s/([a-z0-9-]+)(/.*)?$", path)
        if m and m.group(1) in SITES:
            return m.group(1), m.group(2) or "/", f"/s/{m.group(1)}"
        return None, path, ""

    def handle(
        self,
        method: str,
        host: str,
        target: str,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> Response:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        parts = urlsplit(target)
        query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        cookies = SimpleCookie(headers.get("cookie", ""))
        run = (
            headers.get("x-sandbox-run")
            or query.get("run")
            or (cookies["sbx_run"].value if "sbx_run" in cookies else "")
        )
        new_cookie = False
        if not run:
            run, new_cookie = "run-" + secrets.token_hex(6), True
        if not RUN_RE.match(run):
            return self._finish(html("<h1>Bad run id</h1>", "Error", 400), run, False)
        site, path, prefix = self._resolve_site(host, parts.path)
        resp = self._route(method, site, path, prefix, query, headers, body, run)
        return self._finish(resp, run, new_cookie)

    def _route(self, method, site, path, prefix, query, headers, body, run) -> Response:
        if path == "/robots.txt":
            return Response(kind="raw", ctype="text/plain", body=b"User-agent: *\nDisallow: /\n")
        if path == "/__reset" and method == "POST":
            self.store.reset(run)
            return js({"run": run, "reset": True})
        if site is None:
            links = "".join(
                f'<li><a href="/s/{n}/">{esc(s["title"])}</a> ({n})</li>'
                for n, s in sorted(SITES.items())
            )
            return html(f"<h1>Hands sandbox world</h1><ul>{links}</ul>", "Sandbox world")
        tenant = self.store.get(run)
        if path == "/__state":
            return self._state(site, tenant, headers)
        form: dict[str, str] = {}
        files: list[dict[str, Any]] = []
        json_body = None
        ctype = headers.get("content-type", "")
        if body and ctype.startswith("application/x-www-form-urlencoded"):
            form = {
                k: v[0]
                for k, v in parse_qs(
                    body.decode("utf-8", "replace"), keep_blank_values=True
                ).items()
            }
        elif body and ctype.startswith("multipart/form-data"):
            form, files = parse_multipart(ctype, body)
        elif body and ctype.startswith("application/json"):
            try:
                json_body = json.loads(body)
            except ValueError:
                return js({"error": "invalid json"}, 400)
        ctx = Ctx(
            method,
            path,
            query,
            form,
            files,
            headers,
            json_body,
            site,
            tenant,
            prefix,
            self.store.clock,
        )
        return SITES[site]["router"].dispatch(ctx)

    def _state(self, site: str, tenant: Tenant, headers: dict[str, str]) -> Response:
        if self.public:
            supplied = headers.get(STATE_TOKEN_HEADER, "")
            if not self.state_token or not hmac.compare_digest(supplied, self.state_token):
                # indistinguishable from a missing route: the oracle is not advertised publicly
                return html("<h1>Not found</h1>", "Error", 404)
        return js({"run": tenant.run, "site": site, "state": tenant.state(site)})

    def _finish(self, resp: Response, run: str, new_cookie: bool) -> Response:
        if resp.kind == "html":
            resp.body = (
                f'<!doctype html><html lang="en"><head><meta charset="utf-8">{ROBOTS_META}'
                f"<title>{esc(resp.title or 'Sandbox')} - Sandbox</title></head>"
                f"<body>{BANNER}<main>{resp.body}</main></body></html>"
            ).encode()
        elif resp.kind == "json":
            resp.body = json.dumps(resp.body, sort_keys=True).encode()
        resp.headers["X-Robots-Tag"] = ROBOTS_HEADER
        resp.headers["X-Sandbox"] = "1"
        resp.headers["Cache-Control"] = "no-store"
        if new_cookie:
            # a site that sets its own cookie (``session``) keeps it; the run id then rides the URL
            resp.headers.setdefault("Set-Cookie", f"sbx_run={run}; Path=/; HttpOnly; SameSite=Lax")
        return resp
