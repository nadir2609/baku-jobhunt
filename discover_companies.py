"""Discover Baku IT/software companies via Serper + Firecrawl.

Writes candidates.json (raw pool). Curation into the final 50 happens after.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from providers import serper_organic, firecrawl_scrape, CreditsExhausted

ROOT = Path(__file__).parent
OUT = ROOT / "candidates.json"

# Directories, aggregators, social, news - useful as *sources* but never as companies.
NON_COMPANY_HOSTS = {
    "techbehemoths.com", "clutch.co", "themanifest.com", "goodfirms.co",
    "designrush.com", "edvido.com", "elioplus.com", "glassdoor.com",
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "wikipedia.org", "crunchbase.com", "zoominfo.com",
    "indeed.com", "upwork.com", "medium.com", "github.com", "google.com",
    "apple.com", "microsoft.com", "play.google.com", "t.me", "wa.me",
    "boss.az", "hellojob.az", "busy.az", "jobsearch.az", "smartjob.az",
    "tiktok.com", "pinterest.com", "reddit.com", "quora.com", "trustpilot.com",
    "bing.com", "yandex.com", "wordpress.org", "wix.com", "godaddy.com",
    "sciencedirect.com", "researchgate.net", "issuu.com", "slideshare.net",
}

SEARCH_QUERIES = [
    # English discovery
    ("best IT companies in Baku Azerbaijan", "en"),
    ("top software development companies Baku Azerbaijan", "en"),
    ("software outsourcing company Azerbaijan Baku", "en"),
    ("IT solutions company Baku Azerbaijan official website", "en"),
    ("fintech software company Baku Azerbaijan", "en"),
    ("mobile app development company Baku Azerbaijan", "en"),
    ("web development agency Baku Azerbaijan", "en"),
    ("system integrator IT company Azerbaijan", "en"),
    ("cybersecurity company Baku Azerbaijan", "en"),
    ("ERP software company Azerbaijan", "en"),
    ("data science artificial intelligence company Baku Azerbaijan", "en"),
    ("startup tech company Baku Azerbaijan", "en"),
    ("Azerbaijan High Tech Park resident IT companies", "en"),
    ("IT company Baku careers vacancies contact", "en"),
    # Azerbaijani - where a real Google index matters most
    ("Bakı IT şirkətləri siyahı", "az"),
    ("proqram təminatı şirkəti Bakı", "az"),
    ("İT şirkəti Bakı vakansiya", "az"),
    ("veb sayt hazırlanması şirkəti Bakı", "az"),
    ("mobil tətbiq hazırlanması Bakı şirkət", "az"),
    ("informasiya texnologiyaları şirkəti Azərbaycan", "az"),
    ("proqramlaşdırma şirkəti Bakı əlaqə", "az"),
]

# Directory listing pages worth scraping with Firecrawl for outbound company links.
DIRECTORY_PAGES = [
    "https://techbehemoths.com/companies/baku",
    "https://techbehemoths.com/companies/azerbaijan",
    "https://themanifest.com/az/software-development/companies",
    "https://www.edvido.com/software-companies/azerbaijan",
    "https://clutch.co/az/developers",
    "https://www.goodfirms.co/companies/azerbaijan",
]


def host_of(url):
    try:
        h = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def is_non_company(host):
    if not host or "." not in host:
        return True
    return any(host == b or host.endswith("." + b) for b in NON_COMPANY_HOSTS)


def clean_name(title, host):
    """Derive a company name from a SERP title or link text."""
    if not title:
        return host
    # Titles are usually "Name - tagline | Site"
    name = re.split(r"\s+[|–—]\s+|\s+-\s+", title.strip())[0]
    name = re.sub(r"\s*\(.*?\)\s*$", "", name).strip(" .,:;–—-")
    if len(name) < 2 or len(name) > 60:
        return host
    return name


def add(pool, host, name, source):
    if is_non_company(host):
        return
    entry = pool.setdefault(
        host, {"name": name, "host": host, "website": f"https://{host}", "sources": []}
    )
    if source not in entry["sources"]:
        entry["sources"].append(source)
    # Prefer a human-looking name over a bare hostname.
    if entry["name"] == host and name != host:
        entry["name"] = name


def from_search(pool):
    for query, hl in SEARCH_QUERIES:
        try:
            hits = serper_organic(query, num=20, gl="az", hl=hl)
        except CreditsExhausted as e:
            print(f"STOPPING: {e}")
            raise
        print(f"  [{len(hits):>2}] {query}")
        for h in hits:
            link = h.get("link", "")
            host = host_of(link)
            if is_non_company(host):
                continue
            add(pool, host, clean_name(h.get("title", ""), host), f"serper:{query}")


def from_directories(pool):
    for page in DIRECTORY_PAGES:
        try:
            data = firecrawl_scrape(page)
        except CreditsExhausted as e:
            print(f"STOPPING: {e}")
            raise
        if not data:
            print(f"  [ x] {page}")
            continue
        md = data.get("markdown", "") or ""
        # Company names in these directories appear as markdown links.
        found = 0
        for label, url in re.findall(r"\[([^\]]{2,60})\]\((https?://[^)]+)\)", md):
            host = host_of(url)
            if is_non_company(host):
                continue
            label = label.strip()
            if not label or label.lower().startswith(("http", "read more", "view")):
                continue
            add(pool, host, label, f"directory:{host_of(page)}")
            found += 1
        print(f"  [{found:>2}] {page}")


def main():
    pool = {}
    print("== Serper discovery ==")
    from_search(pool)
    print(f"\npool after search: {len(pool)}")

    print("\n== Firecrawl directory scraping ==")
    from_directories(pool)
    print(f"\npool after directories: {len(pool)}")

    rows = sorted(pool.values(), key=lambda r: -len(r["sources"]))
    OUT.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {OUT} with {len(rows)} candidates")
    for r in rows[:40]:
        print(f"  {len(r['sources'])}x  {r['name'][:40]:<42} {r['host']}")


if __name__ == "__main__":
    main()
