"""Read the Gmail Sent folder for a given date and extract recipient addresses.

Used to exclude companies already contacted. Writes sent_yesterday.json.
"""

import argparse
import email
import imaplib
import json
import os
import sys
from datetime import date, datetime, timedelta
from email.utils import getaddresses
from pathlib import Path

from providers import load_env

ROOT = Path(__file__).parent
OUT = ROOT / "sent_yesterday.json"

SENT_FOLDER_CANDIDATES = [
    '"[Gmail]/Sent Mail"',
    '"[Google Mail]/Sent Mail"',
    '"Sent"',
]


def imap_date(d):
    return d.strftime("%d-%b-%Y")


def connect():
    load_env()
    user = os.environ.get("GMAIL_USER", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not user or not pw:
        sys.exit("ERROR: GMAIL_USER / GMAIL_APP_PASSWORD not set in .env")
    m = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        m.login(user, pw)
    except imaplib.IMAP4.error as e:
        sys.exit(
            f"ERROR: Gmail login failed: {e}\n"
            "Check that 2-Step Verification is on and the App Password is correct "
            "(16 chars, no spaces)."
        )
    return m


def select_sent(m):
    for folder in SENT_FOLDER_CANDIDATES:
        typ, _ = m.select(folder, readonly=True)
        if typ == "OK":
            return folder
    # Fall back to scanning the folder list for a \Sent flagged mailbox.
    typ, boxes = m.list()
    if typ == "OK":
        for raw in boxes:
            line = raw.decode(errors="replace")
            if "\\Sent" in line:
                name = line.split(' "/" ')[-1].strip()
                if m.select(name, readonly=True)[0] == "OK":
                    return name
    sys.exit("ERROR: could not open the Sent Mail folder.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--date",
        default=(date.today() - timedelta(days=1)).isoformat(),
        help="YYYY-MM-DD, defaults to yesterday",
    )
    args = ap.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date()
    nxt = target + timedelta(days=1)

    m = connect()
    folder = select_sent(m)
    print(f"Opened {folder}")

    typ, data = m.search(
        None, f'(SINCE "{imap_date(target)}" BEFORE "{imap_date(nxt)}")'
    )
    ids = data[0].split() if typ == "OK" and data and data[0] else []
    print(f"Messages sent on {target}: {len(ids)}")

    addresses, domains, messages = set(), set(), []
    for mid in ids:
        typ, raw = m.fetch(mid, "(BODY.PEEK[HEADER])")
        if typ != "OK" or not raw or not raw[0]:
            continue
        msg = email.message_from_bytes(raw[0][1])
        recips = [
            addr.lower()
            for _, addr in getaddresses(
                msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Bcc", [])
            )
            if "@" in addr
        ]
        for a in recips:
            addresses.add(a)
            domains.add(a.split("@")[-1])
        messages.append(
            {
                "subject": msg.get("Subject", ""),
                "date": msg.get("Date", ""),
                "to": recips,
            }
        )

    m.logout()

    out = {
        "date": target.isoformat(),
        "message_count": len(ids),
        "addresses": sorted(addresses),
        "domains": sorted(domains),
        "messages": messages,
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nDistinct recipient addresses: {len(addresses)}")
    for a in sorted(addresses):
        print("  ", a)
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
