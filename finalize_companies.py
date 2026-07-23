"""Trim the scraped pool to the final 50 and fold in yesterday's companies.

Ranking favours companies that are reachable, Azerbaijani, and have a usable
address - so the 50 that survive are the ones actually worth emailing.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
COMPANIES = ROOT / "companies.json"
SENT_YESTERDAY = ROOT / "sent_yesterday.json"
FINAL = ROOT / "companies_final.json"

TARGET = 50


def pretty(host):
    base = host.split(".")[0].replace("-", " ")
    return base.upper() if len(base) <= 4 else base.title()


def score(c):
    """Higher is better."""
    s = 0
    if c.get("main_email", "0") != "0":
        s += 40
    if c.get("hr_email", "0") != "0":
        s += 25
    if c.get("reachable"):
        s += 15
    if c.get("az_signal"):
        s += 10
    if c.get("host", "").endswith(".az"):
        s += 5
    # An own-domain address beats a free-mail one.
    for f in ("main_email", "hr_email"):
        a = c.get(f, "0")
        if a != "0" and c["host"].split(".")[0] in a.split("@")[-1]:
            s += 8
    s += min(len(c.get("sources", [])), 4) * 2
    return s


def main():
    companies = json.loads(COMPANIES.read_text(encoding="utf-8"))
    yest = json.loads(SENT_YESTERDAY.read_text(encoding="utf-8"))
    y_addrs = {a.lower() for a in yest.get("addresses", [])}
    y_domains = {d.lower() for d in yest.get("domains", [])}

    scraped = [c for c in companies if "main_email" in c]

    # Drop entries that are dead ends: unreachable AND no address found.
    usable = [
        c
        for c in scraped
        if c.get("reachable") or c.get("main_email", "0") != "0" or c.get("hr_email", "0") != "0"
    ]
    print(f"scraped {len(scraped)} -> usable {len(usable)}")

    # Companies emailed yesterday belong in the sheet even if discovery missed them.
    known_domains = {c["host"].lower() for c in usable}
    added = []
    for dom in sorted(y_domains):
        if dom in known_domains:
            continue
        addrs = sorted(a for a in y_addrs if a.endswith("@" + dom))
        hr = next((a for a in addrs if re.match(r"^(hr|cv|career|job|work)", a)), "0")
        main = next((a for a in addrs if a != hr), "0")
        added.append(
            {
                "name": pretty(dom),
                "host": dom,
                "website": f"https://{dom}",
                "sources": ["gmail-sent-2026-07-22"],
                "main_email": main,
                "hr_email": hr,
                "reachable": True,
                "az_signal": True,
            }
        )
    print(f"added {len(added)} companies seen only in yesterday's Sent mail")

    ranked = sorted(usable, key=score, reverse=True)

    # Everything mailed yesterday is retained regardless of rank, so the
    # "Already Emailed Yesterday" column is complete.
    def mailed_yesterday(c):
        addrs = {
            a.lower()
            for a in (c.get("main_email", "0"), c.get("hr_email", "0"))
            if a != "0"
        }
        return c["host"].lower() in y_domains or bool(addrs & y_addrs)

    keep = [c for c in ranked if mailed_yesterday(c)] + added
    rest = [c for c in ranked if not mailed_yesterday(c)]
    keep += rest[: max(0, TARGET - len(keep))]

    for c in keep:
        c.pop("all_emails", None)
        c.pop("error", None)

    FINAL.write_text(json.dumps(keep, indent=2, ensure_ascii=False), encoding="utf-8")

    with_main = sum(1 for c in keep if c.get("main_email", "0") != "0")
    with_hr = sum(1 for c in keep if c.get("hr_email", "0") != "0")
    n_yest = sum(1 for c in keep if mailed_yesterday(c))
    sends = sum(
        len([a for a in (c.get("main_email", "0"), c.get("hr_email", "0")) if a != "0"])
        for c in keep
        if not mailed_yesterday(c)
    )

    print(f"\nFinal list: {len(keep)}")
    print(f"  with main email:      {with_main}")
    print(f"  with HR email:        {with_hr}")
    print(f"  emailed yesterday:    {n_yest} (excluded from today's send)")
    print(f"  messages to send:     {sends}")
    print(f"\nWrote {FINAL}")


if __name__ == "__main__":
    main()
