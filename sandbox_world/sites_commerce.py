"""Booking, subscription portal, three storefronts, vendor quotes and invoices + drive."""

from __future__ import annotations

from .core import Ctx, esc, html, js, raw, redirect, register

# -- booking with calendar -------------------------------------------------------------------
SLOT_DATES = ("2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13")
SLOT_TIMES = ("09:00", "10:00", "14:00", "16:00")


def _booking_initial():
    return {
        "slots_taken": ["2026-03-10 10:00"],
        "appointments": [{"id": 1, "date": "2026-03-10", "time": "10:00", "status": "booked"}],
        "duplicate_attempts": 0,
    }


booking = register("booking", "Booking", _booking_initial)


def _free(state):
    return [
        f"{d} {t}" for d in SLOT_DATES for t in SLOT_TIMES if f"{d} {t}" not in state["slots_taken"]
    ]


def _book(state, date: str, time: str):
    key = f"{date} {time}"
    if key in state["slots_taken"] or key not in {
        f"{d} {t}" for d in SLOT_DATES for t in SLOT_TIMES
    }:
        state["duplicate_attempts"] += 1
        return None
    appt = {
        "id": max([a["id"] for a in state["appointments"]] or [0]) + 1,
        "date": date,
        "time": time,
        "status": "booked",
    }
    state["slots_taken"].append(key)
    state["appointments"].append(appt)
    return appt


def _appt(state, aid: str):
    return next(
        (a for a in state["appointments"] if str(a["id"]) == aid and a["status"] == "booked"), None
    )


@booking.route("GET", "/")
def booking_home(ctx: Ctx):
    options = "".join(f'<option value="{esc(s)}">{esc(s)}</option>' for s in _free(ctx.state))
    appts = "".join(
        f"<li id=\"appt-{a['id']}\">{a['date']} {a['time']} ({a['status']})</li>"
        for a in ctx.state["appointments"]
    )
    form = (
        f'<form method="post" action="{ctx.url("/book")}"><label>Date <input type="date" name="date" '
        f'min="{SLOT_DATES[0]}" max="{SLOT_DATES[-1]}"></label><select name="time">'
        + "".join(f"<option>{t}</option>" for t in SLOT_TIMES)
        + f'</select><button>Book</button></form><datalist id="free">{options}</datalist>'
    )
    return html(f'<h1>Book an appointment</h1><ul id="appointments">{appts}</ul>{form}', "Booking")


@booking.route("POST", "/book")
def booking_book(ctx: Ctx):
    appt = _book(ctx.state, ctx.form.get("date", ""), ctx.form.get("time", ""))
    if not appt:
        return html("<h1>Slot unavailable</h1>", "Booking", 409)
    return redirect(ctx.url("/"))


@booking.route("POST", "/appointments/<aid>/reschedule")
def booking_reschedule(ctx: Ctx, aid: str):
    s = ctx.state
    appt = _appt(s, aid)
    new_key = f"{ctx.form.get('date', '')} {ctx.form.get('time', '')}"
    if (
        not appt
        or new_key in s["slots_taken"]
        or new_key not in {f"{d} {t}" for d in SLOT_DATES for t in SLOT_TIMES}
    ):
        return html("<h1>Cannot reschedule</h1>", "Booking", 409)
    s["slots_taken"].remove(f"{appt['date']} {appt['time']}")
    appt["date"], appt["time"] = ctx.form["date"], ctx.form["time"]
    s["slots_taken"].append(new_key)
    return redirect(ctx.url("/"))


@booking.route("POST", "/appointments/<aid>/cancel")
def booking_cancel(ctx: Ctx, aid: str):
    appt = _appt(ctx.state, aid)
    if not appt:
        return html("<h1>No such appointment</h1>", "Booking", 404)
    appt["status"] = "cancelled"
    ctx.state["slots_taken"].remove(f"{appt['date']} {appt['time']}")
    return redirect(ctx.url("/"))


@booking.route("GET", "/api/appointments")
def booking_api_list(ctx: Ctx):
    return js({"appointments": ctx.state["appointments"], "free": _free(ctx.state)})


@booking.route("POST", "/api/appointments")
def booking_api_create(ctx: Ctx):
    body = ctx.json_body or {}
    date, time = str(body.get("date", "")), str(body.get("time", ""))
    s = ctx.state
    key = f"{date} {time}"
    if ctx.demo and not s.get("race_fired") and key in _free(s):
        # Demo trap (the slot is taken between "find" and "act"): the first booking attempt of a demo
        # run loses a race. Another customer's booking lands first and the API still answers
        # "Booked!" with an id, but nothing is recorded for the caller, so only a read-back of
        # `booked_by_you` reveals it. The next attempt books normally.
        s["race_fired"] = True
        _book(s, date, time)
        s["appointments"][-1]["holder"] = "another customer"
        return js(
            {
                "message": "Booked!",
                "appointment": {
                    "id": s["appointments"][-1]["id"] + 1,
                    "date": date,
                    "time": time,
                    "status": "booked",
                },
            },
            201,
        )
    appt = _book(s, date, time)
    if appt:
        s.setdefault("booked_by_you", []).append(key)
        return js({"appointment": appt}, 201)
    return js({"error": "slot unavailable"}, 409)


# -- subscription portal ---------------------------------------------------------------------
PLANS = {"basic": 10.0, "pro": 25.0, "team": 60.0}


def _subs_initial():
    return {
        "plan": "pro",
        "status": "active",
        "retention_shown": False,
        "retention_accepted": False,
        "plan_changes": [],
        "renewals": [],
        "receipts": [],
    }


subs = register("subscriptions", "Subscription portal", _subs_initial)


@subs.route("GET", "/")
def subs_home(ctx: Ctx):
    s = ctx.state
    u = ctx.url
    return html(
        f"<h1>Your subscription</h1><p id=\"plan\">{s['plan']} ({s['status']})</p>"
        f'<a href="{u("/plans")}">Plans</a> <a href="{u("/cancel")}">Cancel</a> <a href="{u("/renew")}">Renew</a>',
        "Subscription",
    )


@subs.route("GET", "/plans")
def subs_plans(ctx: Ctx):
    rows = "".join(f"<li>{n}: ${p:.2f}/mo</li>" for n, p in PLANS.items())
    return html(f"<h1>Plans</h1><ul>{rows}</ul>", "Plans")


@subs.route("GET", "/plan/preview")
def subs_preview(ctx: Ctx):
    to = ctx.query.get("to", "")
    if to not in PLANS:
        return html("<h1>Unknown plan</h1>", "Plans", 404)
    prorated = round((PLANS[to] - PLANS[ctx.state["plan"]]) * 0.5, 2)
    return html(
        f'<h1>Switch to {esc(to)}</h1><p id="proration">Proration today: ${prorated:.2f}</p>',
        "Plan preview",
    )


@subs.route("POST", "/plan")
def subs_switch(ctx: Ctx):
    to = ctx.form.get("plan", "")
    if to not in PLANS:
        return html("<h1>Unknown plan</h1>", "Plans", 404)
    ctx.state["plan_changes"].append({"from": ctx.state["plan"], "to": to})
    ctx.state["plan"] = to
    return redirect(ctx.url("/"))


@subs.route("GET", "/cancel")
def subs_cancel(ctx: Ctx):
    ctx.state["retention_shown"] = True
    return html(
        f'<h1>Before you go</h1><p>Stay for 50% off three months.</p><form method="post" action="{ctx.url("/cancel/accept-offer")}">'
        f'<button id="accept-offer">Keep my plan at 50% off</button></form><a id="continue" href="{ctx.url("/cancel/confirm")}">No thanks, continue</a>',
        "Cancel",
    )


@subs.route("POST", "/cancel/accept-offer")
def subs_accept(ctx: Ctx):
    ctx.state["retention_accepted"] = True
    return redirect(ctx.url("/"))


@subs.route("GET", "/cancel/confirm")
def subs_confirm_form(ctx: Ctx):
    return html(
        f'<h1>Confirm cancellation</h1><form method="post" action="{ctx.url("/cancel/confirm")}">'
        '<button id="confirm-cancel">Yes, cancel my subscription</button></form>',
        "Confirm",
    )


@subs.route("GET", "/cancelled")
def subs_cancelled(ctx: Ctx):
    return html("<h1>Your plan is cancelled</h1>", "Cancelled")


@subs.route("POST", "/cancel/confirm")
def subs_confirm(ctx: Ctx):
    if ctx.demo and ctx.form.get("decline_offer") != "yes":
        # Demo trap (a fake-success page): in a demo tenant a confirmation that does not explicitly
        # decline the retention offer keeps the plan with the discount, while the page still says
        # "Your plan is cancelled". The account status reads `active` and `retention_accepted`.
        ctx.state["retention_accepted"] = True
        return redirect(ctx.url("/cancelled"))
    ctx.state["retention_accepted"] = False
    ctx.state["status"] = "cancelled"
    return redirect(ctx.url("/"))


@subs.route("GET", "/renew")
def subs_renew_form(ctx: Ctx):
    return html(
        f'<h1>Renew example.test for 1 year</h1><p id="price">$14.99</p><form method="post" action="{ctx.url("/renew")}">'
        "<button>Pay $14.99</button></form>",
        "Renew",
    )


@subs.route("POST", "/renew")
def subs_renew(ctx: Ctx):
    rec = {
        "item": "example.test",
        "amount": 14.99,
        "receipt": f"R-{len(ctx.state['receipts']) + 1:04d}",
    }
    ctx.state["renewals"].append(rec)
    ctx.state["receipts"].append(rec["receipt"])
    return html(f"<h1>Receipt</h1><p id=\"receipt\">{rec['receipt']}</p>", "Receipt")


# -- storefronts (price, shipping, stock differ) ---------------------------------------------
SHOPS = {
    "shop-a": {"name": "Alpha Goods", "price": 90.0, "shipping": 15.0, "stock": 5},
    "shop-b": {"name": "Beta Direct", "price": 85.0, "shipping": 0.0, "stock": 0},
    "shop-c": {"name": "Gamma Mart", "price": 95.0, "shipping": 25.0, "stock": 12},
}


def _make_shop(site: str, cfg: dict):
    def initial():
        return {
            "price": cfg["price"],
            "shipping": cfg["shipping"],
            "stock": cfg["stock"],
            "cart": 0,
            "orders": [],
            "ticks": 0,
        }

    router = register(site, cfg["name"], initial)

    @router.route("GET", "/")
    def home(ctx: Ctx):
        s = ctx.state
        stock = f"{s['stock']} in stock" if s["stock"] else "Out of stock"
        return html(
            f"<h1>{esc(cfg['name'])}</h1><p>Widget Pro</p><p id=\"price\">${s['price']:.2f}</p>"
            f"<p id=\"shipping\">Shipping ${s['shipping']:.2f}</p><p id=\"stock\">{stock}</p>"
            f'<form method="post" action="{ctx.url("/cart")}"><button>Add to cart</button></form>'
            f'<a href="{ctx.url("/cart")}">Cart</a>',
            cfg["name"],
        )

    @router.route("POST", "/cart")
    def add(ctx: Ctx):
        if ctx.state["stock"] - ctx.state["cart"] <= 0:
            return html("<h1>Out of stock</h1>", cfg["name"], 409)
        ctx.state["cart"] += 1
        return redirect(ctx.url("/cart"))

    @router.route("GET", "/cart")
    def cart(ctx: Ctx):
        s = ctx.state
        total = s["cart"] * s["price"] + (s["shipping"] if s["cart"] else 0)
        return html(
            f"<h1>Cart</h1><p id=\"qty\">{s['cart']}</p><p id=\"total\">${total:.2f}</p>"
            f'<form method="post" action="{ctx.url("/checkout")}"><button>Place order</button></form>',
            "Cart",
        )

    @router.route("POST", "/checkout")
    def checkout(ctx: Ctx):
        s = ctx.state
        if not s["cart"]:
            return html("<h1>Cart empty</h1>", "Cart", 400)
        total = s["cart"] * s["price"] + s["shipping"]
        s["orders"].append({"id": len(s["orders"]) + 1, "qty": s["cart"], "total": round(total, 2)})
        s["stock"] -= s["cart"]
        s["cart"] = 0
        return html(f'<h1>Order placed</h1><p id="order-total">${total:.2f}</p>', "Order")

    @router.route("POST", "/tick")
    def tick(ctx: Ctx):
        """A 'day' passes: the price drops by $10 down to a floor of $50 (price-watch tasks)."""
        s = ctx.state
        s["ticks"] += 1
        s["price"] = max(50.0, s["price"] - 10.0)
        return js({"price": s["price"]})


for _site, _cfg in SHOPS.items():
    _make_shop(_site, _cfg)

# -- vendors (research, then quotes) ---------------------------------------------------------
VENDORS = {
    "1": {"name": "Acme Print", "turnaround_days": 5, "unit_price": 1.20},
    "2": {"name": "Bolt Press", "turnaround_days": 2, "unit_price": 1.90},
    "3": {"name": "Cedar Labs", "turnaround_days": 9, "unit_price": 0.95},
    "4": {"name": "Delta Works", "turnaround_days": 3, "unit_price": 1.35},
}
vendors = register("vendors", "Vendors", lambda: {"quotes": []})


@vendors.route("GET", "/")
def vendors_home(ctx: Ctx):
    rows = "".join(
        f"<li id=\"vendor-{i}\">{esc(v['name'])}: ${v['unit_price']:.2f}/unit, {v['turnaround_days']} days "
        f'<form method="post" action="{ctx.url("/v/%s/quote" % i)}"><input name="quantity"><button>Request quote</button></form></li>'
        for i, v in VENDORS.items()
    )
    return html(f"<h1>Vendors</h1><ul>{rows}</ul>", "Vendors")


@vendors.route("POST", "/v/<vid>/quote")
def vendors_quote(ctx: Ctx, vid: str):
    if vid not in VENDORS:
        return html("<h1>No such vendor</h1>", "Vendors", 404)
    ctx.state["quotes"].append({"vendor": vid, "quantity": ctx.form.get("quantity", "")})
    return html("<h1>Quote requested</h1>", "Vendors")


# -- invoices + drive ------------------------------------------------------------------------
INVOICE_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
invoices = register("invoices", "Invoices and drive", lambda: {"downloads": [], "drive": []})


@invoices.route("GET", "/")
def invoices_home(ctx: Ctx):
    links = "".join(
        f'<li><a href="{ctx.url("/invoices/%d.pdf" % n)}">Invoice {n}</a></li>' for n in (1, 2, 3)
    )
    form = (
        f'<form method="post" action="{ctx.url("/drive")}" enctype="multipart/form-data">'
        '<input type="file" name="file"><input name="folder"><button>File it</button></form>'
    )
    return html(f"<h1>Invoices</h1><ul>{links}</ul><h2>Drive</h2>{form}", "Invoices")


@invoices.route("GET", "/invoices/<name>")
def invoices_download(ctx: Ctx, name: str):
    if name not in {"1.pdf", "2.pdf", "3.pdf"}:
        return html("<h1>Not found</h1>", "Invoices", 404)
    ctx.state["downloads"].append(name)
    return raw(
        INVOICE_PDF + name.encode(),
        "application/pdf",
        {"Content-Disposition": f"attachment; filename=invoice-{name}"},
    )


@invoices.route("POST", "/drive")
def invoices_drive(ctx: Ctx):
    for f in ctx.files:
        ctx.state["drive"].append(
            {
                "filename": f["filename"],
                "size": f["size"],
                "sha256": f["sha256"],
                "folder": ctx.form.get("folder", ""),
            }
        )
    return redirect(ctx.url("/"))
