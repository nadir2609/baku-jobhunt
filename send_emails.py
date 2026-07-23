"""Send the CV to each company address.

Modes, to be run strictly in this order:
  --dry-run   print the recipient table and message preview, send nothing
  --test      send exactly one real message to GMAIL_USER
  --live      send to every company address (requires explicit user approval first)

One message per address: a company with both a main and an HR email gets two.
Every attempt is appended to send_log.json immediately, so an interruption never
loses state and a re-run skips addresses already logged as sent.
"""

import argparse
import json
import mimetypes
import os
import smtplib
import ssl
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from providers import load_env

ROOT = Path(__file__).parent
COMPANIES = ROOT / "companies_final.json"
SENT_YESTERDAY = ROOT / "sent_yesterday.json"
LOG = ROOT / "send_log.json"
CV = ROOT / "nadir_askarov_cv.pdf"

SUBJECT = "AI Engineer & Data Scientist vacancy"
BODY = ""  # intentionally empty, per spec
DELAY_SECONDS = 25


def load_log():
    if LOG.exists():
        try:
            return json.loads(LOG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return []


def append_log(entry):
    log = load_log()
    log.append(entry)
    LOG.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")


def build_message(sender, to_addr):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = SUBJECT
    msg.set_content(BODY)

    ctype, _ = mimetypes.guess_type(CV.name)
    maintype, subtype = (ctype or "application/pdf").split("/", 1)
    msg.add_attachment(
        CV.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=CV.name,
    )
    return msg


def excluded_domains():
    if not SENT_YESTERDAY.exists():
        return set(), set()
    data = json.loads(SENT_YESTERDAY.read_text(encoding="utf-8"))
    return set(data.get("addresses", [])), set(data.get("domains", []))


def build_recipients():
    """-> list of (company_name, which, address), plus the excluded company names."""
    if not COMPANIES.exists():
        sys.exit(f"ERROR: {COMPANIES} not found - run discovery/scraping first.")
    companies = json.loads(COMPANIES.read_text(encoding="utf-8"))
    skip_addrs, skip_domains = excluded_domains()

    recipients, skipped = [], []
    for c in companies:
        name = c.get("name", "?")
        pairs = [
            ("main", c.get("main_email", "0")),
            ("HR", c.get("hr_email", "0")),
        ]
        live = [(w, a) for w, a in pairs if a and a != "0"]
        if not live:
            continue
        # Domain-level match: mailed at hr@x.az yesterday excludes info@x.az today.
        doms = {a.split("@")[-1].lower() for _, a in live}
        if doms & skip_domains or {a.lower() for _, a in live} & skip_addrs:
            skipped.append(name)
            continue
        for which, addr in live:
            recipients.append((name, which, addr))
    return recipients, skipped


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--test", action="store_true")
    g.add_argument("--live", action="store_true")
    ap.add_argument("--delay", type=int, default=DELAY_SECONDS)
    args = ap.parse_args()

    load_env()
    sender = os.environ.get("GMAIL_USER", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()

    if not CV.exists():
        sys.exit(f"ERROR: {CV} not found.")
    print(f"Attachment: {CV.name} ({CV.stat().st_size:,} bytes)")
    print(f"Subject:    {SUBJECT}")
    print(f"Body:       (empty)\n")

    if args.dry_run:
        recipients, skipped = build_recipients()
        print(f"{'COMPANY':<38} {'WHICH':<6} ADDRESS")
        print("-" * 90)
        for name, which, addr in recipients:
            print(f"{name[:37]:<38} {which:<6} {addr}")
        print("-" * 90)
        print(f"\nWould send {len(recipients)} messages.")
        if skipped:
            print(f"Excluded (emailed yesterday): {len(skipped)}")
            for s in skipped:
                print("   -", s)
        if not SENT_YESTERDAY.exists():
            print("\nWARNING: sent_yesterday.json missing - no exclusions applied yet.")
        print("\nNothing was sent (dry run).")
        return

    if not sender or not pw:
        sys.exit("ERROR: GMAIL_USER / GMAIL_APP_PASSWORD not set in .env")

    if args.test:
        targets = [("SELF TEST", "test", sender)]
    else:
        targets, skipped = build_recipients()
        already = {e["to"] for e in load_log() if e.get("status") == "sent"}
        before = len(targets)
        targets = [t for t in targets if t[2] not in already]
        if before != len(targets):
            print(f"Resuming: {before - len(targets)} already sent, skipping those.\n")

    ctx = ssl.create_default_context()
    sent = failed = 0
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as smtp:
        smtp.login(sender, pw)
        for i, (name, which, addr) in enumerate(targets, 1):
            entry = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "company": name,
                "which": which,
                "to": addr,
            }
            try:
                smtp.send_message(build_message(sender, addr))
                entry["status"] = "sent"
                sent += 1
                print(f"[{i}/{len(targets)}] sent -> {addr}  ({name}, {which})")
            except smtplib.SMTPException as e:
                entry["status"] = "failed"
                entry["error"] = str(e)[:300]
                failed += 1
                print(f"[{i}/{len(targets)}] FAILED -> {addr}: {e}")
                # 4xx means Gmail is throttling: stop rather than hammer it.
                if isinstance(e, smtplib.SMTPResponseException) and 400 <= e.smtp_code < 500:
                    append_log(entry)
                    print("\nGmail returned a 4xx - stopping. Re-run --live to resume.")
                    break
            append_log(entry)
            if i < len(targets):
                time.sleep(args.delay)

    print(f"\nDone. sent={sent} failed={failed}")


if __name__ == "__main__":
    main()
