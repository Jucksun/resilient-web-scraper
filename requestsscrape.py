"""
Web scraper + crawler (v2)
===========================

WHAT THIS DOES:
  - Grabs all H1s, all paragraph text, all links, and the raw HTML source
    from a page.
  - Crawls MULTIPLE pages, following links out across different websites
    (not just the one you start on) -- EXCEPT Google / Google Search,
    which is always blocked.
  - Skips duplicate language versions of the same page (e.g. /es/, /fr/).
  - Filters which links to follow based on a list of keyword "tags" you
    give it (matched against the link text and URL).

HOW TO USE (the only part you probably need to edit is the very bottom,
under `if __name__ == "__main__":`, unless you want to include alternative features.)
"""

import requests
from bs4 import BeautifulSoup
import time
import json
import re
import os
from urllib.parse import urljoin, urlparse

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Referer": "https://www.google.com/",
}

# Sites I NEVER want to crawl, no matter what
BLOCKED_DOMAINS = {
    "google.com", "www.google.com", "maps.google.com",
    "accounts.google.com", "policies.google.com", "support.google.com",
}

# URL path segments that mean this is a non-english language version
# Add/remove language codes here as needed for the site youre crawling
LANG_CODE_PATTERN = re.compile(
    r"/(es|fr|de|it|pt|zh|ja|ko|ru|ar|nl|pl|tr|sv|no|da|fi)([/-]|$)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Single-page scraping
# ---------------------------------------------------------------------------

def scrape(url, session=None, timeout=10, retries=3, backoff=1.5):
    """Fetch a URL. Returns (raw_html_source, BeautifulSoup_object)."""
    req = session or requests
    last_exc = None

    for attempt in range(1, retries + 1):
        try:
            response = req.get(url, headers=BROWSER_HEADERS, timeout=timeout)
            response.raise_for_status()
            return response.text, BeautifulSoup(response.text, "html.parser")
        except requests.exceptions.RequestException as e:
            last_exc = e
            print(f"  Attempt {attempt}/{retries} failed for {url}: {e}")
            if attempt < retries:
                time.sleep(backoff ** attempt)

    raise RuntimeError(f"Failed to fetch {url} after {retries} attempts") from last_exc


def extract_page_data(soup, base_url, html_source=None):
    """Pull ALL h1s, ALL p's, all links (absolute), and the raw source."""
    h1_tags = [h.get_text(strip=True) for h in soup.find_all("h1")]
    p_tags = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]

    links = []
    seen_urls = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        full_url = urljoin(base_url, href)
        full_url = full_url.split("#")[0]  # drop #fragment junk
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        links.append({"url": full_url, "text": a.get_text(strip=True)})

    return {
        "url": base_url,
        "h1_tags": h1_tags,
        "p_tags": p_tags,
        "links": links,
        "source": html_source,
    }


# 
# Multi-page, multi-domain crawler


class TaggedCrawler:
    """
    Crawls starting from seed_urls and follows links, including onto
    OTHER domains, unless a domain is in blocked_domains (Google is
    always blocked by default).

    tags: list[str], ["news", "energy"]
        A link is only followed if its URL or its visible text contains
        one of these words (case-insensitive). Leave as None/[] to
        follow every link (within scope).

    same_domain_only: set True if you want to stay on the starting
        site only, instead of branching out to other domains.
    """

    def __init__(
        self,
        seed_urls,
        tags=None,
        max_depth=2,
        max_pages=50,
        same_domain_only=False,
        blocked_domains=None,
        skip_language_variants=True,
        delay=1.0,
        timeout=10,
        retries=3,
    ):
        self.seed_urls = seed_urls if isinstance(seed_urls, list) else [seed_urls]
        self.tags = [t.lower() for t in tags] if tags else []
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.same_domain_only = same_domain_only
        self.blocked_domains = set(blocked_domains) if blocked_domains else set(BLOCKED_DOMAINS)
        self.skip_language_variants = skip_language_variants
        self.delay = delay
        self.timeout = timeout
        self.retries = retries

        self.allowed_domains = {self._normalize_domain(u) for u in self.seed_urls}
        self.visited = set()
        self.results = {}

    @staticmethod
    def _normalize_domain(url):
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc

    def _is_blocked(self, url):
        domain = self._normalize_domain(url)
        return any(domain == b or domain.endswith("." + b) for b in self.blocked_domains)

    def _in_scope(self, url):
        if self._is_blocked(url):
            return False
        if not self.same_domain_only:
            return True
        return self._normalize_domain(url) in self.allowed_domains

    def _is_language_variant(self, url):
        if not self.skip_language_variants:
            return False
        return bool(LANG_CODE_PATTERN.search(urlparse(url).path))

    def _link_matches_tags(self, link):
        if not self.tags:
            return True
        haystack = f"{link['url']} {link['text']}".lower()
        return any(tag in haystack for tag in self.tags)

    def crawl(self):
        with requests.Session() as session:
            queue = [(u, 0) for u in self.seed_urls]

            while queue and len(self.visited) < self.max_pages:
                url, depth = queue.pop(0)

                if url in self.visited:
                    continue
                if depth > self.max_depth:
                    continue
                if not self._in_scope(url):
                    continue
                if self._is_language_variant(url):
                    continue

                print(f"[depth {depth}] Fetching: {url}")
                self.visited.add(url)

                try:
                    html_source, soup = scrape(
                        url, session=session, timeout=self.timeout, retries=self.retries
                    )
                except RuntimeError as e:
                    print(f"  Skipping (failed): {e}")
                    continue

                data = extract_page_data(soup, url, html_source=html_source)
                self.results[url] = data
                print(f"  -> {len(data['h1_tags'])} h1s, {len(data['p_tags'])} paragraphs, {len(data['links'])} links")

                if depth < self.max_depth:
                    for link in data["links"]:
                        next_url = link["url"]
                        if (
                            next_url not in self.visited
                            and self._in_scope(next_url)
                            and not self._is_language_variant(next_url)
                            and self._link_matches_tags(link)
                        ):
                            queue.append((next_url, depth + 1))

                time.sleep(self.delay)

        print(f"\nDone. Visited {len(self.visited)} pages total.")
        return self.results

    def save_json(self, path="crawl_results.json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(self.results)} pages of data to {path}")

    def save_sources(self, out_dir="page_sources"):
        os.makedirs(out_dir, exist_ok=True)
        for i, (url, data) in enumerate(self.results.items()):
            fname = os.path.join(out_dir, f"page_{i}.html")
            with open(fname, "w", encoding="utf-8") as f:
                f.write(data["source"] or "")
        print(f"Saved {len(self.results)} raw HTML files to {out_dir}/")



# THIS IS THE PART USERS EDIT


if __name__ == "__main__":

    crawler = TaggedCrawler(
        seed_urls=["https://news.ycombinator.com"],  # <-- site to start from
        tags=[
            "partner", "directory", "member", "company", "business"
            ], # <-- to filter, or None for everything
        max_depth=3,                  # <-- how many link hops away from the start to go
        max_pages=100,                 # <-- stop after visiting this many pages total
        same_domain_only=False,       # <-- False = allowed to follow links to OTHER websites tooo
        skip_language_variants=True,  # <-- skip /es/ /fr/ /de/ and more duplicate pages
        delay=1.0,                    # <-- seconds to wait between requests (be polite)
    )

    results = crawler.crawl()
    crawler.save_json("crawl_results.json")
    crawler.save_sources("page_sources")
