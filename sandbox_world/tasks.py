"""Which sandbox site (and `/__state` oracle key) each delegation-benchmark task T01-T25 needs.

The task fixtures themselves belong to #39808; this table is the contract between that benchmark and
the sites. Every oracle key is a top-level key of that site's `/__state` document.
"""

from __future__ import annotations

TASK_SITES: dict[str, dict[str, object]] = {
    "T01": {"sites": ["booking"], "oracle": ["appointments", "slots_taken"]},
    "T02": {"sites": ["booking"], "oracle": ["appointments", "duplicate_attempts"]},
    "T03": {"sites": ["subscriptions"], "oracle": ["status", "retention_accepted"]},
    "T04": {"sites": ["subscriptions"], "oracle": ["plan", "plan_changes"]},
    "T05": {"sites": ["support", "mail"], "oracle": ["tickets", "outbox"]},
    "T06": {"sites": ["support"], "oracle": ["tickets"]},
    "T07": {"sites": ["subscriptions"], "oracle": ["renewals", "receipts"]},
    "T08": {"sites": ["govform"], "oracle": ["submissions", "uploads", "resume_codes"]},
    "T09": {"sites": ["taxform"], "oracle": ["submissions"]},
    "T10": {"sites": ["shop-a", "shop-b", "shop-c"], "oracle": ["orders", "cart"]},
    "T11": {"sites": ["accounts"], "oracle": ["accounts"]},
    "T12": {"sites": ["shop-a"], "oracle": ["orders", "price", "ticks"]},
    "T13": {"sites": ["vendors"], "oracle": ["quotes"]},
    "T14": {"sites": ["mailbot", "mail"], "oracle": ["deal", "offers"]},
    "T15": {"sites": ["invoices"], "oracle": ["downloads", "drive"]},
    "T16": {"sites": ["typeform", "mail"], "oracle": ["answers", "completed"]},
    "T17": {"sites": ["bank", "mail"], "oracle": ["stage", "failed_logins"]},
    "T18": {"sites": ["signup", "mail"], "oracle": ["accounts"]},
    "T19": {"sites": ["traps"], "oracle": ["injection_followed"]},
    "T20": {"sites": ["traps"], "oracle": ["interstitial_consent", "bypass_attempts"]},
    "T21": {"sites": ["traps"], "oracle": ["fact_answers"]},
    "T22": {"sites": ["govform"], "oracle": ["signature", "video"]},
    "T23": {"sites": ["support"], "oracle": ["tickets", "orders"]},
    "T24": {"sites": ["booking"], "oracle": ["appointments"]},
    "T25": {"sites": ["traps"], "oracle": ["layout_submissions", "stale_selector_posts"]},
}
