"""Cookie-session site for the Hands e2e flow (#40272).

Unlike ``bank`` and ``accounts`` (signed-in state kept server-side per run), sign-in here sets an
``sbx_auth`` cookie, so a captured storage state replays into a later session-backed read task. The
data page renders only with that cookie, which makes "the read completed with the expected data" a
meaningful assertion: a task without a valid session sees the login form instead.
"""

from __future__ import annotations

import secrets

from .core import Ctx, esc, html, redirect, register

USERNAME = "hands-e2e"
PASSWORD = "pw-hands-e2e"
AUTH_COOKIE = "sbx_auth"
# What the read task must extract; the e2e asserts on this exact string.
DATA_MARKER = "HANDS-E2E-ACCOUNT-BALANCE-4242.00"


def _initial():
    return {"token": "", "logins": 0, "failed_logins": 0, "reads": 0, "denied_reads": 0}


session_site = register("session", "Cookie-session account", _initial)


def _cookie(ctx: Ctx, name: str) -> str:
    for part in ctx.headers.get("cookie", "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return ""


def _authed(ctx: Ctx) -> bool:
    token = ctx.state["token"]
    return bool(token) and secrets.compare_digest(_cookie(ctx, AUTH_COOKIE), token)


@session_site.route("GET", "/")
def session_home(ctx: Ctx):
    if _authed(ctx):
        return html(
            f'<h1>Signed in</h1><p>Welcome back.</p><a href="{ctx.url("/data")}">Account data</a>',
            "Account",
        )
    return redirect(ctx.url("/login"))


# Fixed pixel layout: the e2e drives this form through the remote browser's pointer events, so the
# click targets are constants (see LOGIN_USERNAME_XY / LOGIN_PASSWORD_XY / LOGIN_SUBMIT_XY).
LOGIN_USERNAME_XY = (300, 140)
LOGIN_PASSWORD_XY = (300, 220)
LOGIN_SUBMIT_XY = (200, 300)
_LOGIN_CSS = (
    "<style>body{margin:0}h1{position:absolute;left:100px;top:20px;margin:0}"
    "input,button{position:absolute;left:100px;width:400px;height:44px;font-size:20px}"
    "#username{top:118px}#password{top:198px}button{top:278px;width:200px}</style>"
)


@session_site.route("GET", "/login")
def session_login_form(ctx: Ctx):
    return html(
        f'{_LOGIN_CSS}<h1>Sign in</h1><form method="post" action="{ctx.url("/login")}">'
        '<input id="username" name="username" autocomplete="off">'
        '<input id="password" name="password" type="password">'
        '<button type="submit">Sign in</button></form>',
        "Sign in",
    )


@session_site.route("POST", "/login")
def session_login(ctx: Ctx):
    s = ctx.state
    if ctx.form.get("username") != USERNAME or ctx.form.get("password") != PASSWORD:
        s["failed_logins"] += 1
        return html("<h1>Wrong credentials</h1>", "Sign in", 401)
    s["token"] = secrets.token_hex(16)
    s["logins"] += 1
    resp = redirect(ctx.url("/"))
    resp.headers["Set-Cookie"] = f"{AUTH_COOKIE}={s['token']}; Path=/; HttpOnly; SameSite=Lax"
    return resp


@session_site.route("GET", "/data")
def session_data(ctx: Ctx):
    if not _authed(ctx):
        ctx.state["denied_reads"] += 1
        return redirect(ctx.url("/login"))
    ctx.state["reads"] += 1
    return html(f'<h1>Account data</h1><p id="balance">{esc(DATA_MARKER)}</p>', "Account data")
