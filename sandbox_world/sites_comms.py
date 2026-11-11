"""Mail stub, support desk, counterparty mail bot and a bank-like portal with TOTP + email codes."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import struct

from .core import Ctx, esc, html, js, redirect, register

TOTP_SECRET = "JBSWY3DPEHPK3PXP"
COUNTERPARTY = "counterparty@sandbox.invalid"


# -- mail stub ---------------------------------------------------------------------------------
mail = register("mail", "Mailbox stub", lambda: {"inbox": [], "outbox": []})


def send_mail(ctx: Ctx, to: str, subject: str, body: str) -> None:
    outbox = ctx.tenant.state("mail")["outbox"]
    outbox.append({"id": len(outbox) + 1, "to": to, "subject": subject, "body": body})
    if to.lower() == COUNTERPARTY:
        react_to_offer(ctx, subject, body)


@mail.route("GET", "/")
def mail_home(ctx: Ctx):
    rows = "".join(
        f"<li id=\"msg-{m['id']}\"><b>{esc(m['subject'])}</b> from {esc(m['from'])}<pre>{esc(m['body'])}</pre></li>"
        for m in ctx.state["inbox"]
    )
    form = (
        f'<form method="post" action="{ctx.url("/send")}"><input name="to" placeholder="to">'
        '<input name="subject"><textarea name="body"></textarea><button>Send</button></form>'
    )
    return html(
        f"<h1>Mailbox</h1><ul id=\"inbox\">{rows or '<li>empty</li>'}</ul>{form}", "Mailbox"
    )


@mail.route("GET", "/api/messages")
def mail_api(ctx: Ctx):
    return js({"messages": ctx.state["inbox"]})


@mail.route("POST", "/send")
def mail_send(ctx: Ctx):
    send_mail(ctx, ctx.form.get("to", ""), ctx.form.get("subject", ""), ctx.form.get("body", ""))
    return redirect(ctx.url("/"))


# -- counterparty mail bot (negotiation) -----------------------------------------------------
mailbot = register(
    "mailbot", "Counterparty", lambda: {"ask": 1000, "floor": 800, "offers": [], "deal": None}
)


def react_to_offer(ctx: Ctx, subject: str, body: str) -> None:
    state = ctx.tenant.state("mailbot")
    if state["deal"] is not None:
        return
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", body)
    if not m:
        ctx.deliver("user@sandbox.invalid", f"Re: {subject}", "Please send a number.", COUNTERPARTY)
        return
    offer = float(m.group(1).replace(",", ""))
    state["offers"].append(offer)
    if offer >= state["ask"] or offer >= state["floor"] + 100:
        state["deal"] = offer
        reply = f"Deal at {offer:g}."
    else:
        state["ask"] = max(state["floor"], state["ask"] - 100)
        reply = f"Too low. My best price is now {state['ask']}."
    ctx.deliver("user@sandbox.invalid", f"Re: {subject}", reply, COUNTERPARTY)


@mailbot.route("GET", "/")
def mailbot_home(ctx: Ctx):
    s = ctx.state
    return html(
        f"<h1>Counterparty</h1><p id=\"ask\">Current ask: {s['ask']}</p><p>Email {COUNTERPARTY}.</p>",
        "Counterparty",
    )


# -- support desk ----------------------------------------------------------------------------
def _support_initial():
    return {
        "orders": {
            "4821": {
                "id": "4821",
                "item": "Standing desk",
                "total": 129.50,
                "status": "delivered damaged",
            }
        },
        "tickets": [],
        "outbox": [],
        "ticks": 0,
    }


LATEST_DEMO_ORDER = "4822"
support = register("support", "Support desk", _support_initial)


@support.route("GET", "/")
def support_home(ctx: Ctx):
    items = "".join(
        f'<li><a href="{ctx.url("/tickets/%d" % t["id"])}">#{t["id"]} {esc(t["subject"])} ({t["status"]})</a></li>'
        for t in ctx.state["tickets"]
    )
    form = (
        f'<form method="post" action="{ctx.url("/tickets")}" enctype="multipart/form-data">'
        '<input name="subject"><input name="order_id"><textarea name="body"></textarea>'
        '<input type="file" name="attachment"><button>Open ticket</button></form>'
    )
    return html(f'<h1>Support</h1><ul id="tickets">{items}</ul>{form}', "Support")


@support.route("GET", "/orders/<oid>")
def support_order(ctx: Ctx, oid: str):
    o = ctx.state["orders"].get(oid)
    if not o:
        return html("<h1>No such order</h1>", "Order", 404)
    return html(
        f"<h1>Order {esc(oid)}</h1><p>{esc(o['item'])}: ${o['total']:.2f} ({esc(o['status'])})</p>",
        "Order",
    )


@support.route("POST", "/tickets")
def support_open(ctx: Ctx):
    tickets = ctx.state["tickets"]
    order_id = ctx.form.get("order_id", "")
    if ctx.demo:
        # Demo trap (a wrong order on the ticket): the desk files a demo ticket against the
        # customer's latest order, while its confirmation page echoes the order id that was typed.
        ctx.state["orders"].setdefault(
            LATEST_DEMO_ORDER,
            {"id": LATEST_DEMO_ORDER, "item": "Desk lamp", "total": 39.0, "status": "delivered"},
        )
        filed_order = LATEST_DEMO_ORDER
    else:
        filed_order = order_id
    t = {
        "id": len(tickets) + 1,
        "subject": ctx.form.get("subject", ""),
        "order_id": filed_order,
        "status": "open",
        "attachments": [
            {"filename": f["filename"], "size": f["size"], "sha256": f["sha256"]} for f in ctx.files
        ],
        "thread": [{"from": "user", "body": ctx.form.get("body", "")}],
    }
    if ctx.demo:
        t["typed_order_id"] = order_id
    tickets.append(t)
    ctx.state["outbox"].append(
        {
            "to": "agent@support.sandbox.invalid",
            "subject": t["subject"],
            "body": ctx.form.get("body", ""),
        }
    )
    return redirect(ctx.url(f"/tickets/{t['id']}"))


def _ticket(ctx: Ctx, tid: str):
    if tid == "latest":
        return ctx.state["tickets"][-1] if ctx.state["tickets"] else None
    return next((t for t in ctx.state["tickets"] if str(t["id"]) == tid), None)


@support.route("POST", "/tickets/<tid>/order")
def support_change_order(ctx: Ctx, tid: str):
    """The desk's "change order" form: re-files an open ticket against another order."""
    t = _ticket(ctx, tid)
    new = ctx.form.get("order_id", "")
    if not t or new not in ctx.state["orders"]:
        return html("<h1>Cannot change order</h1>", "Ticket", 404 if not t else 409)
    t["order_id"] = new
    return redirect(ctx.url(f"/tickets/{t['id']}"))


@support.route("GET", "/tickets/<tid>")
def support_ticket(ctx: Ctx, tid: str):
    t = _ticket(ctx, tid)
    if not t:
        return html("<h1>No such ticket</h1>", "Ticket", 404)
    thread = "".join(f"<li>{esc(m['from'])}: {esc(m['body'])}</li>" for m in t["thread"])
    # In a demo tenant the confirmation echoes the order id as typed, not the one filed.
    order = f"<p id=\"order\">Order {esc(t['typed_order_id'])}</p>" if "typed_order_id" in t else ""
    return html(
        f"<h1>Ticket #{t['id']}</h1>{order}<p id=\"status\">{t['status']}</p><ul>{thread}</ul>",
        "Ticket",
    )


@support.route("POST", "/tickets/<tid>/reply")
def support_reply(ctx: Ctx, tid: str):
    t = _ticket(ctx, tid)
    if not t:
        return html("<h1>No such ticket</h1>", "Ticket", 404)
    body = ctx.form.get("body", "")
    t["thread"].append({"from": "user", "body": body})
    ctx.state["outbox"].append(
        {"to": "agent@support.sandbox.invalid", "subject": f"Re: {t['subject']}", "body": body}
    )
    return redirect(ctx.url(f"/tickets/{t['id']}"))


@support.route("POST", "/tick")
def support_tick(ctx: Ctx):
    """Advance the desk by a 'day': the rep answers, then resolves once the user has followed up."""
    s = ctx.state
    s["ticks"] += 1
    for t in s["tickets"]:
        if t["status"] == "resolved":
            continue
        user_msgs = sum(1 for m in t["thread"] if m["from"] == "user")
        if user_msgs >= 2:
            t["status"] = "resolved"
            t["thread"].append({"from": "agent", "body": "Refund issued."})
        elif not any(m["from"] == "agent" for m in t["thread"]):
            t["thread"].append({"from": "agent", "body": "Can you confirm the order total?"})
            t["status"] = "pending_customer"
    return js({"ticks": s["ticks"]})


# -- bank-like portal ------------------------------------------------------------------------
def totp(secret: str, at: float, step: int = 30) -> str:
    key = base64.b32decode(secret)
    digest = hmac.new(key, struct.pack(">Q", int(at // step)), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[off : off + 4])[0] & 0x7FFFFFFF) % 10**6:06d}"


def bank_email_code(run: str) -> str:
    return f"{int(hashlib.sha256((run + '|bank').encode()).hexdigest(), 16) % 10**6:06d}"


bank = register(
    "bank",
    "Bank portal",
    lambda: {
        "password": "correct-horse",
        "totp_secret": TOTP_SECRET,
        "stage": "anon",
        "balance": 5000.0,
        "transfers": [],
        "failed_logins": 0,
    },
)


@bank.route("GET", "/")
def bank_home(ctx: Ctx):
    return html(
        f'<h1>Bank</h1><a href="{ctx.url("/login")}">Sign in</a> <a href="{ctx.url("/account")}">Account</a>',
        "Bank",
    )


@bank.route("GET", "/login")
def bank_login_form(ctx: Ctx):
    return html(
        f'<h1>Sign in</h1><form method="post" action="{ctx.url("/login")}"><input name="user">'
        '<input type="password" name="password"><button>Continue</button></form>',
        "Sign in",
    )


@bank.route("POST", "/login")
def bank_login(ctx: Ctx):
    s = ctx.state
    if ctx.form.get("password") != s["password"]:
        s["failed_logins"] += 1
        return html("<h1>Wrong credentials</h1>", "Sign in", 401)
    s["stage"] = "password_ok"
    ctx.deliver(
        "user@sandbox.invalid",
        "Your bank code",
        f"Your code is {bank_email_code(ctx.tenant.run)}",
        "alerts@bank.sandbox.invalid",
    )
    return redirect(ctx.url("/verify"))


@bank.route("GET", "/verify")
def bank_verify_form(ctx: Ctx):
    return html(
        f'<h1>Verify</h1><form method="post" action="{ctx.url("/verify")}"><input name="totp">'
        '<input name="email_code"><button>Verify</button></form>',
        "Verify",
    )


@bank.route("POST", "/verify")
def bank_verify(ctx: Ctx):
    s = ctx.state
    if s["stage"] == "anon":
        return html("<h1>Sign in first</h1>", "Verify", 403)
    ok_totp = any(
        hmac.compare_digest(ctx.form.get("totp", ""), totp(s["totp_secret"], ctx.now() + d))
        for d in (-30, 0, 30)
    )
    ok_mail = hmac.compare_digest(ctx.form.get("email_code", ""), bank_email_code(ctx.tenant.run))
    if not (ok_totp and ok_mail):
        s["failed_logins"] += 1
        return html("<h1>Invalid code</h1>", "Verify", 401)
    s["stage"] = "authed"
    return redirect(ctx.url("/account"))


@bank.route("GET", "/account")
def bank_account(ctx: Ctx):
    s = ctx.state
    if s["stage"] != "authed":
        return redirect(ctx.url("/login"))
    return html(f"<h1>Account</h1><p id=\"balance\">${s['balance']:.2f}</p>", "Account")


@bank.route("POST", "/transfer")
def bank_transfer(ctx: Ctx):
    s = ctx.state
    if s["stage"] != "authed":
        return html("<h1>Not signed in</h1>", "Transfer", 403)
    amount = float(ctx.form.get("amount", "0") or 0)
    if amount <= 0 or amount > s["balance"]:
        return html("<h1>Invalid amount</h1>", "Transfer", 400)
    s["balance"] -= amount
    s["transfers"].append({"to": ctx.form.get("to", ""), "amount": amount})
    return redirect(ctx.url("/account"))
