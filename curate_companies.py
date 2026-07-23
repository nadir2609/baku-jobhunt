"""Curate candidates.json down to 50 real Baku IT/software companies.

Removes news, job boards, government agencies, training academies, foreign
directories and asset hosts, then tops up from a list of known Baku IT firms
that the generic queries missed. Writes companies.json.
"""

import json
import re
from pathlib import Path

from providers import serper_organic, CreditsExhausted

ROOT = Path(__file__).parent
CANDIDATES = ROOT / "candidates.json"
OUT = ROOT / "companies.json"

# Hosts that showed up in discovery but are not IT service companies.
REJECT_HOSTS = {
    # news / media
    "report.az", "azernews.az", "apa.az", "ica.az", "ict.az", "bakutime.com",
    "caspianenergy.club", "medium.com",
    # job boards / recruiters
    "vakansiya.az", "easyjob.az", "azjob.az", "jobu.az", "jooble.az",
    "vezife.az", "birjob.com", "hh1.az", "hrcbaku.com", "airswift.com",
    "devsdata.com", "smartjob.az",
    # directories / aggregators / listings
    "2gis.az", "navigator.az", "f6s.com", "tracxn.com", "sortlist.com",
    "ensun.io", "rocketreach.co", "startupblink.com", "failory.com",
    "bakinity.biz", "tender.az", "ithalatihracat.biz", "azerbusiness.az",
    "t.marja.az", "ybcase.com", "caspianlegalcenter.az", "iasp.ws",
    "erpsoftwaresuite.com", "omniful.ai", "topmobileappdevelopmentcompany.com",
    "aesthetixglobal.com", "partners.kompitech.com",
    "cybersecurityintelligence.com", "glassdoor.co.in", "deloitte.com",
    "adb.org", "edvido.ae", "mobiteam.de", "startup.az",
    # education / training
    "ufaz.az", "netacad.az", "dsa.az", "div.edu.az", "coders.edu.az",
    "kurslar.az", "jetacademy.az", "az-baku.com", "adas.edu.az",
    "startupschool.az",
    # finance / associations, not IT vendors
    "finca.az", "finmanagement.az", "azfina.az", "bakufintech.com",
    # state agencies / regulators, not companies
    "idda.az", "icta.az", "akm.az", "cert.az",
    # asset / infra hosts
    "img.shgstatic.com", "imagedelivery.net", "survey.hsforms.com",
    "bakiorme.era.az",
}

REJECT_PATTERNS = [
    r"\.gov\.az$",
    r"\.edu\.az$",
    r"^science\.gov\.az$",
]

# Known Baku IT/software companies worth confirming directly.
KNOWN = [
    ("ATL Tech", "atltech.az"),
    ("Sinam", "sinam.net"),
    ("BestComp Group", "bestcomp.net"),
    ("AzInTelecom", "azintelecom.az"),
    ("PASHA Technology", "pashatech.az"),
    ("Baku IT Lab", "bitl.az"),
    ("Improtex Technologies", "improtex.com"),
    ("Caspel", "caspel.az"),
    ("Azerconnect", "azerconnect.az"),
    ("Deirvlon Technologies", "deirvlon.com"),
    ("Softline Azerbaijan", "softline.az"),
    ("Ultra Technologies", "ultra.az"),
    ("Nexon", "nexon.az"),
    ("Zamanix", "zamanix.com"),
    ("Bakcell Digital", "bakcell.com"),
    ("Simbrella", "simbrella.com"),
    ("AzEuroTel", "azeurotel.com"),
    ("Delta Group", "deltagroup.az"),
    ("Netlab", "netlab.az"),
    ("Azel Technologies", "azel.az"),
    ("Smartsoft", "smartsoft.az"),
    ("Codeway", "codeway.az"),
    ("Prosoft", "prosoft.az"),
    ("Technica", "technica.az"),
    ("Sysnet", "sysnet.az"),
]

TOPUP_QUERIES = [
    "IT şirkəti Bakı proqram təminatı hazırlanması MMC",
    "software company Baku Azerbaijan \"about us\" contact email",
    "digital agency Baku Azerbaijan software development services",
    "outsourcing IT services company Baku Azerbaijan team",
    "Azerbaijan software house Baku custom development",
    "1C ERP proqram təminatı şirkəti Bakı",
    "IT dəstək xidmətləri şirkəti Bakı MMC",
    "Baku Azerbaijan tech company engineering team careers",
]


def host_of(url):
    m = re.match(r"https?://([^/]+)", url or "")
    if not m:
        return ""
    h = m.group(1).lower()
    return h[4:] if h.startswith("www.") else h


def rejected(host):
    if host in REJECT_HOSTS:
        return True
    return any(re.search(p, host) for p in REJECT_PATTERNS)


def tidy_name(name, host):
    """Clean up SERP-derived names into something presentable."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    name = re.sub(
        r"^(Home|Home page|Company|Vacancies|Visit Website|About us|Bizimlə Əlaqə|Əlaqə)$",
        "",
        name,
        flags=re.I,
    )
    # Long descriptive titles are worse than a clean domain-derived name.
    if not name or len(name) > 42 or name.count(" ") > 5:
        base = host.split(".")[0]
        return base.upper() if len(base) <= 4 else base.capitalize()
    return name


def main():
    cands = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    pool = {}

    for c in cands:
        host = c["host"]
        if rejected(host):
            continue
        pool[host] = {
            "name": tidy_name(c["name"], host),
            "host": host,
            "website": c["website"],
            "sources": c.get("sources", [])[:2],
        }

    print(f"after filtering: {len(pool)}")

    # Top up with known firms, confirming each resolves in search.
    for name, host in KNOWN:
        if host in pool or rejected(host):
            continue
        pool[host] = {
            "name": name,
            "host": host,
            "website": f"https://{host}",
            "sources": ["known-baku-it"],
        }
    print(f"after known-firm top-up: {len(pool)}")

    # Extra searches if still short.
    if len(pool) < 60:
        for q in TOPUP_QUERIES:
            try:
                hits = serper_organic(q, num=20, gl="az", hl="az")
            except CreditsExhausted as e:
                print(f"STOPPING: {e}")
                break
            added = 0
            for h in hits:
                host = host_of(h.get("link", ""))
                if not host or host in pool or rejected(host):
                    continue
                from discover_companies import is_non_company

                if is_non_company(host):
                    continue
                pool[host] = {
                    "name": tidy_name(h.get("title", ""), host),
                    "host": host,
                    "website": f"https://{host}",
                    "sources": [f"topup:{q[:30]}"],
                }
                added += 1
            print(f"  +{added:>2}  {q[:60]}")

    rows = list(pool.values())
    print(f"\ntotal pool: {len(rows)}")
    OUT.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT}")
    for i, r in enumerate(rows, 1):
        print(f"{i:>3} {r['host']:<34} {r['name']}")


if __name__ == "__main__":
    main()
