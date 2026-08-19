"""RSS feed fetching and parsing (stdlib only, Python 3.9 compatible).

fetch_feed(url) -> list of item dicts:
    {guid, title, link, published, description, source}
Raises FeedError on network/parse failure so callers can skip one feed
without aborting the whole scan.
"""
import hashlib
import re
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
TIMEOUT = 15
MAX_BYTES = 5_000_000  # 5 MB safety cap per feed

# RSS/Atom namespaces we care about
NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom": "http://www.w3.org/2005/Atom",
}


class FeedError(Exception):
    pass


def _domain(url):
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1) if m else url


def _clean_text(s):
    if not s:
        return ""
    # strip HTML tags from descriptions
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _parse_date(s):
    """Parse RFC-822 (RSS pubDate) or ISO (Atom) dates -> ISO Z string."""
    if not s:
        return None
    s = s.strip()
    try:
        # ISO 8601 (Atom)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        pass
    try:
        # RFC 822 (RSS)
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return None


def _local_name(tag):
    """Strip namespace from ElementTree tag."""
    return tag.rsplit("}", 1)[-1]


def _parse_items(root, source):
    """Parse items from either <channel><item> (RSS) or <feed><entry> (Atom)."""
    items = []

    # find the container of entries
    channel = root.find("channel")
    entry_parent = channel if channel is not None else root
    tag = "item" if channel is not None else "entry"

    for node in entry_parent.findall(tag):
        item = {}
        for child in node:
            name = _local_name(child.tag)
            text = (child.text or "").strip()
            if name in ("title", "link", "guid", "pubDate", "updated", "published", "description", "summary"):
                if name not in item or (name == "link" and not item.get("link")):
                    item[name] = text

        title = item.get("title") or ""
        link = item.get("link") or ""
        guid = item.get("guid") or ""
        published = _parse_date(item.get("pubDate") or item.get("published") or item.get("updated"))
        desc = item.get("description") or item.get("summary") or ""

        # Some feeds put <link> inside <guid> or use guid as url
        if not link:
            link = guid

        # GUID fallback: sha256 over link + normalized title
        if not guid:
            raw = f"{link}|{title}".strip().lower()
            guid = hashlib.sha256(raw.encode("utf-8")).hexdigest()

        if not title and not link:
            continue

        items.append({
            "guid": guid,
            "title": _clean_text(title),
            "link": link,
            "published": published,
            "description": _clean_text(desc),
            "source": source,
        })
    return items


def fetch_feed(url, source=None):
    """Fetch and parse one RSS/Atom feed. Raises FeedError on failure."""
    if source is None:
        source = _domain(url)

    # Python 3.9's redirect handler ignores HTTP 308; add our own opener
    class _Redirect308(urllib.request.HTTPRedirectHandler):
        def http_error_308(self, req, fp, code, msg, headers):
            return self.http_error_302(req, fp, code, msg, headers)

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            # 3.9 hardcodes allowed codes without 308; replicate with 308 added
            m = req.get_method()
            if not (code in (301, 302, 303, 307, 308) and m in ("GET", "HEAD")):
                raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)
            newurl = newurl.replace(" ", "%20")
            import urllib.parse
            return urllib.request.Request(
                urllib.parse.urljoin(req.full_url, newurl),
                headers=req.headers,
                origin_req_host=req.origin_req_host,
                unverifiable=True,
            )

    opener = urllib.request.build_opener(_Redirect308)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    try:
        with opener.open(req, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                raise FeedError(f"{url}: HTTP {resp.status}")
            raw = resp.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise FeedError(f"{url}: feed exceeds {MAX_BYTES} bytes")
    except urllib.error.HTTPError as e:
        raise FeedError(f"{url}: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise FeedError(f"{url}: {e.reason}") from e
    except OSError as e:
        raise FeedError(f"{url}: {e}") from e

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise FeedError(f"{url}: XML parse error: {e}") from e

    # channel title as nicer source name
    ch = root.find("channel")
    if ch is not None:
        ch_title = ch.findtext("title")
        if ch_title and ch_title.strip():
            source = _clean_text(ch_title)

    return _parse_items(root, source)
