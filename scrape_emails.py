"""Scrape main + HR emails from each company's website via Firecrawl.

Also validates that the site is reachable and looks like an Azerbaijani company;
unreachable domains are marked so they can be dropped from the final 50.

Records the literal string "0" for anything not found - never guesses an address.
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from providers import (
    firecrawl_map,
    firecrawl_scrape,
    plain_fetch,
    serper_organic,
    CreditsExhausted,
)

ROOT = Path(__file__).parent
COMPANIES = ROOT / "companies.json"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+-]+)\s*(?:\(at\)|\[at\]|\s+at\s+)\s*([A-Za-z0-9.-]+\.[A-Za-z]{2,})",
    re.I,
)

CONTACT_HINTS = re.compile(
    r"contact|about|career|vacanc|hr|team|jobs|"
    r"elaqe|əlaqə|haqq|karyera|vakansiya|bize-|bizimle",
    re.I,
)

MAIN_PREFIXES = ("info@", "contact@", "office@", "mail@", "hello@", "sales@",
                 "welcome@", "support@", "admin@")
HR_PATTERN = re.compile(
    r"^(hr[a-z]*|career[s]?|job[s]?|recruit\w*|cv|vacanc\w*|vakansiya\w*|"
    r"karyera\w*|talent\w*|hiring|insanresurslari|people)(?=[@._-])", re.I
)

# Junk that regex-matches an email but is not one.
BAD_EMAIL = re.compile(
    r"(\.(png|jpe?g|gif|svg|webp|css|js|woff2?|ttf|ico|pdf|mp4)$)"
    r"|(@(2x|3x)\.)"
    r"|(example\.(com|org))|(sentry\.io)|(wixpress\.com)|(@domain\.)"
    r"|(@company\.)|(@yourcompany)|(@yoursite)|(@mysite)|(@site\.com)"
    r"|(@email\.com)|(@gmail\.co$)|(zohoforms)|(jotform)|(hsforms)"
    r"|(yourname)|(email@)|(your@)|(name@)|(user@)|(test@)"
    r"|(\.png@)|(sentry)|(@sentry)|(godaddy)|(cloudflare)|(w3\.org)"
    r"|(schema\.org)|(\.webp)|(@2x)",
    re.I,
)

AZ_SIGNAL = re.compile(
    r"\b(baku|bakı|bakida|bakıda|azerbaijan|azərbaycan|azerbaycan|\.az\b)", re.I
)


def valid_email(addr):
    addr = addr.strip().strip(".,;:()<>\"'").lower()
    if not addr or BAD_EMAIL.search(addr):
        return None
    local, _, domain = addr.partition("@")
    if not local or not domain or "." not in domain:
        return None
    if len(local) > 64 or len(addr) > 100:
        return None
    if domain.split(".")[-1].isdigit():
        return None
    return addr


def harvest(text, links):
    """Pull candidate addresses out of markdown text plus a links array."""
    found = []
    for raw in EMAIL_RE.findall(text or ""):
        e = valid_email(raw)
        if e:
            found.append(e)
    for a, b in OBFUSCATED_RE.findall(text or ""):
        e = valid_email(f"{a}@{b}")
        if e:
            found.append(e)
    for link in links or []:
        if isinstance(link, str) and link.lower().startswith("mailto:"):
            e = valid_email(link[7:].split("?")[0])
            if e:
                found.append(e)
    return found


def pick_urls(host, website, mapped):
    """Homepage plus up to 5 contact/career-ish pages from the site map."""
    urls = [website]
    if mapped:
        raw = mapped.get("links") or mapped.get("data") or []
        cand = []
        for item in raw:
            u = item.get("url") if isinstance(item, dict) else item
            if isinstance(u, str) and host in u and CONTACT_HINTS.search(u):
                cand.append(u)
        # Shortest URLs first: /contact beats /blog/2019/contact-us-form
        cand.sort(key=len)
        for u in cand:
            if u not in urls:
                urls.append(u)
            if len(urls) >= 6:
                break
    return urls


def classify(emails, host):
    """Split harvested addresses into a main and an HR address."""
    base = host.split(".")[0].lower()
    own, foreign = [], []
    for e in emails:
        dom = e.split("@")[-1].lower()
        dom_base = dom.split(".")[0]
        if dom.startswith("www."):
            dom_base = dom.split(".")[1] if dom.count(".") > 1 else dom_base
        # Same brand only when the registrable label matches exactly. Substring
        # matching wrongly accepted risk.az -> info@riskkazakhstan.com, which is
        # a different country's office.
        if dom == host or dom_base == base:
            own.append(e)
        elif dom in ("gmail.com", "mail.ru", "yandex.ru", "inbox.ru", "bk.ru",
                     "outlook.com", "hotmail.com", "yahoo.com"):
            foreign.append(e)
    ordered = own + foreign

    hr = next((e for e in ordered if HR_PATTERN.match(e)), None)
    main = next(
        (e for e in ordered if e.startswith(MAIN_PREFIXES) and e != hr), None
    )
    if not main:
        main = next((e for e in ordered if e != hr), None)
    return main or "0", hr or "0"


# Conventional contact-page paths, tried over free direct HTTP before Firecrawl.
GUESS_PATHS = [
    "", "/contact", "/contacts", "/contact-us", "/about", "/about-us",
    "/careers", "/career", "/jobs", "/vacancies",
    "/elaqe", "/əlaqə", "/haqqimizda", "/karyera", "/vakansiyalar",
    "/en/contact", "/az/elaqe", "/en/about", "/contact.php", "/contact.html",
]

HREF_MAILTO_RE = re.compile(r'href=["\']mailto:([^"\'?]+)', re.I)


def plain_stage(host, website):
    """Free direct HTTP over conventional paths. Returns (text, links, reachable)."""
    texts, links, reachable = [], [], False
    base = website.rstrip("/")
    for path in GUESS_PATHS:
        html = plain_fetch(base + path)
        if not html:
            continue
        reachable = True
        texts.append(html)
        links.extend(f"mailto:{m}" for m in HREF_MAILTO_RE.findall(html))
        # Stop early once we have an address from a real contact page.
        if path and EMAIL_RE.search(html):
            break
    return "\n".join(texts), links, reachable


def process(company):
    host, website = company["host"], company["website"]

    # Stage 1: free direct HTTP. Most small .az sites are plain HTML.
    blob, links, reachable = plain_stage(host, website)
    emails = harvest(blob, links)
    company["method"] = "http" if emails else ""

    # Stage 2: Firecrawl only where free fetching came up empty (rate-limited).
    if not emails:
        try:
            mapped = firecrawl_map(website, limit=120)
            for url in pick_urls(host, website, mapped)[:4]:
                data = firecrawl_scrape(url)
                if data:
                    reachable = True
                    blob += "\n" + (data.get("markdown") or "")
                    links.extend(data.get("links") or [])
        except CreditsExhausted:
            raise
        emails = harvest(blob, links)
        if emails:
            company["method"] = "firecrawl"

    # Last resort: a cheap search, in case the address is published elsewhere.
    if not emails:
        try:
            for hit in serper_organic(f'"{company["name"]}" {host} email contact', num=10):
                emails.extend(
                    harvest(f"{hit.get('title','')} {hit.get('snippet','')}", [])
                )
        except CreditsExhausted:
            raise
        emails = [e for e in emails if host in e.split("@")[-1]]

    main, hr = classify(emails, host)
    company["main_email"] = main
    company["hr_email"] = hr
    company["reachable"] = reachable
    company["az_signal"] = bool(AZ_SIGNAL.search(blob)) or host.endswith(".az")
    company["all_emails"] = sorted(set(emails))[:12]
    return company


def main():
    companies = json.loads(COMPANIES.read_text(encoding="utf-8"))
    todo = [c for c in companies if "main_email" not in c]
    print(f"{len(companies)} companies, {len(todo)} still to scrape\n")

    done = 0
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(process, c): c for c in todo}
            for fut in as_completed(futures):
                c = futures[fut]
                try:
                    fut.result()
                except CreditsExhausted as e:
                    print(f"\nSTOPPING: {e}")
                    break
                except Exception as e:  # noqa: BLE001 - never lose the whole run
                    c["main_email"] = c.get("main_email", "0")
                    c["hr_email"] = c.get("hr_email", "0")
                    c["reachable"] = False
                    c["error"] = str(e)[:200]
                done += 1
                flag = "" if c.get("reachable") else "  (unreachable)"
                print(
                    f"[{done:>3}/{len(todo)}] {c['host']:<28} "
                    f"main={c.get('main_email','0'):<30} hr={c.get('hr_email','0')}{flag}"
                )
                COMPANIES.write_text(
                    json.dumps(companies, indent=2, ensure_ascii=False), encoding="utf-8"
                )
    finally:
        COMPANIES.write_text(
            json.dumps(companies, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    scraped = [c for c in companies if "main_email" in c]
    with_email = [c for c in scraped if c["main_email"] != "0" or c["hr_email"] != "0"]
    print(f"\nscraped:       {len(scraped)}")
    print(f"reachable:     {sum(1 for c in scraped if c.get('reachable'))}")
    print(f"with >=1 email:{len(with_email)}")
    print(f"with HR email: {sum(1 for c in scraped if c.get('hr_email','0') != '0')}")


if __name__ == "__main__":
    main()
