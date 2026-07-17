#!/usr/bin/env python3
"""
Phone / Tablet Prices (CellphoneS, Hoang Ha Mobile, The Gioi Di Dong, FPT
Shop, ...) -> Email  (runs on GitHub Actions, no local computer needed)

Same shape as tuongphantrue's other *-mailer scripts (gold-price-emailer,
house-price-emailer, tech-price-mailer): fetch, then email an HTML digest
via Gmail SMTP, in two phases so the workflow can persist dedup state
*between* them:

    python phone_tablet_price_emailer.py generate
        -> scrapes each configured retailer/category page, writes the
           composed email (subject/html/text) under ./email/, and updates
           the "last sent price" state file

    python phone_tablet_price_emailer.py send
        -> reads ./email/* and sends it via Gmail SMTP

SOURCE & AN IMPORTANT CAVEAT
-----------------------------
There is no clean, structured, frequently-updated "market price" table for
phones/tablets the way giavang.org publishes for gold. What this script
does instead is scrape *listing prices* directly off each retailer's own
category pages. These are each store's current asking prices (often
already discounted), NOT a market average - treat the email as "what each
listed store is charging right now for the items on page 1 of that
category," not as an authoritative price index. Always check the live
page before buying anything.

A second, bigger caveat specific to this domain: several major Vietnamese
phone retailers (CellphoneS in particular) render their product grids via
JavaScript/AJAX rather than server-side HTML, which a plain `requests`
GET cannot see - a run will report 0 parsed items for those pages. This
script is written against server-rendered listing pages; if you point it
at a retailer whose category page loads products client-side, you'll need
either (a) a different, more static page on that retailer's site (their
older "true" category pages / SEO landing pages sometimes still
server-render), or (b) a browser-automation tool (Playwright/Selenium)
instead of `requests`. See "Which retailers actually work" in README.md.

The parser (`parse_listing()`) matches by *text adjacency* - a
product-name-looking line immediately followed by a "X.XXX.XXX đ" / "₫"
price line - rather than by exact HTML structure, so it should survive
minor theme/markup changes better than a strict DOM walk. If a run
reports 0 parsed items for a retailer, the page either doesn't
server-render its listings at all, or the layout changed more than
adjacency-matching can handle - open the URL and check `parse_listing()`.

SETUP
-----
1. Install dependencies:
   pip install requests beautifulsoup4 certifi

2. Create a Gmail "App Password" (regular Gmail passwords won't work with SMTP):
   - Go to https://myaccount.google.com/apppasswords
   - You need 2-Step Verification turned on first.
   - Create an app password for "Mail" and copy the 16-character code.

3. Set these as environment variables (see README.md for GitHub Actions
   secrets instead, if running in the cloud):

   export GMAIL_ADDRESS="youraddress@gmail.com"
   export GMAIL_APP_PASSWORD="16-char-app-password"
   export PHONE_RECIPIENT="where-to-send@example.com"
   export SEND_ONLY_ON_CHANGE="false"          # optional, default false
   export TIMEZONE="Asia/Ho_Chi_Minh"          # optional, for the subject line
   export MAX_ITEMS_PER_CATEGORY="12"          # optional
   export STATE_FILE="state/last_price.json"   # optional, dedup state file
   export ALLOW_INSECURE_SSL_FALLBACK="false"  # optional, last-resort TLS bypass

   # Optional per-retailer URL overrides (see RETAILERS below for defaults
   # and env-var names, e.g. CELLPHONES_PHONE_URL, FPTSHOP_TABLET_URL, ...)

NOTE ON SCRAPING
-----------------
Always worth checking the current robots.txt / terms of whatever site
this is pointed at before running it unattended long-term, e.g.:
    https://cellphones.com.vn/robots.txt
    https://hoanghamobile.com/robots.txt
    https://www.thegioididong.com/robots.txt
    https://fptshop.com.vn/robots.txt

This is a personal price-watch tool, not investment or purchase advice -
always confirm the actual price on the retailer's site before buying.
"""

import hashlib
import json
import os
import re
import smtplib
import ssl
import sys
import unicodedata
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from urllib.parse import urljoin

import certifi
import requests
import urllib3
from bs4 import BeautifulSoup

if os.environ.get("ALLOW_INSECURE_SSL_FALLBACK", "false").lower() == "true":
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Retailers & categories to scrape. Every entry is independent - if one
# retailer's page doesn't server-render (see module docstring), that entry
# will just report 0 parsed items and the rest still run fine.
#
# Every URL is overridable via its own env var, e.g.:
#   CELLPHONES_PHONE_URL, CELLPHONES_TABLET_URL,
#   HOANGHAMOBILE_PHONE_URL, HOANGHAMOBILE_TABLET_URL,
#   THEGIOIDIDONG_PHONE_URL, THEGIOIDIDONG_TABLET_URL,
#   FPTSHOP_PHONE_URL, FPTSHOP_TABLET_URL
# ---------------------------------------------------------------------------
RETAILERS = [
    {
        "key": "cellphones_phone",
        "retailer": "CellphoneS",
        "category": "Điện thoại",
        "url": os.environ.get("CELLPHONES_PHONE_URL", "https://cellphones.com.vn/mobile.html"),
    },
    {
        "key": "cellphones_tablet",
        "retailer": "CellphoneS",
        "category": "Tablet",
        "url": os.environ.get("CELLPHONES_TABLET_URL", "https://cellphones.com.vn/tablet.html"),
    },
    {
        "key": "hoanghamobile_phone",
        "retailer": "Hoàng Hà Mobile",
        "category": "Điện thoại",
        "url": os.environ.get("HOANGHAMOBILE_PHONE_URL", "https://hoanghamobile.com/dien-thoai-di-dong"),
    },
    {
        "key": "hoanghamobile_tablet",
        "retailer": "Hoàng Hà Mobile",
        "category": "Tablet",
        "url": os.environ.get("HOANGHAMOBILE_TABLET_URL", "https://hoanghamobile.com/may-tinh-bang"),
    },
    {
        "key": "thegioididong_phone",
        "retailer": "Thế Giới Di Động",
        "category": "Điện thoại",
        "url": os.environ.get("THEGIOIDIDONG_PHONE_URL", "https://www.thegioididong.com/dtdd"),
    },
    {
        "key": "thegioididong_tablet",
        "retailer": "Thế Giới Di Động",
        "category": "Tablet",
        "url": os.environ.get("THEGIOIDIDONG_TABLET_URL", "https://www.thegioididong.com/may-tinh-bang"),
    },
    {
        "key": "fptshop_phone",
        "retailer": "FPT Shop",
        "category": "Điện thoại",
        "url": os.environ.get("FPTSHOP_PHONE_URL", "https://fptshop.com.vn/dien-thoai"),
    },
    {
        "key": "fptshop_tablet",
        "retailer": "FPT Shop",
        "category": "Tablet",
        "url": os.environ.get("FPTSHOP_TABLET_URL", "https://fptshop.com.vn/may-tinh-bang"),
    },
]

# Comma-separated list of retailer keys to actually run this time, e.g.
# "cellphones_phone,cellphones_tablet". Empty/unset = run all of RETAILERS.
_ONLY = os.environ.get("ONLY_RETAILER_KEYS", "").strip()
if _ONLY:
    _wanted = {k.strip() for k in _ONLY.split(",") if k.strip()}
    RETAILERS = [r for r in RETAILERS if r["key"] in _wanted]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

EMAIL_DIR = "email"
STATE_FILE = os.environ.get("STATE_FILE", "state/last_price.json")
SEND_ONLY_ON_CHANGE = os.environ.get("SEND_ONLY_ON_CHANGE", "false").lower() == "true"
ALLOW_INSECURE_SSL_FALLBACK = os.environ.get("ALLOW_INSECURE_SSL_FALLBACK", "false").lower() == "true"
MAX_ITEMS_PER_CATEGORY = int(os.environ.get("MAX_ITEMS_PER_CATEGORY", "12"))

# Matches Vietnamese-formatted currency like "1.990.000 ₫" / "1.990.000 đ"
# (dot as thousands separator). A listing/discount line often has two of
# these back to back: sale price, then the crossed-out original price.
PRICE_RE = re.compile(r"([\d]{1,3}(?:\.[\d]{3})+)\s*(?:\u20ab|đ\b)", re.IGNORECASE)

# Lines that are clearly chrome/navigation/filters, not product names -
# skip these even if a price happens to follow within lookahead range.
# Kept broad since this points at several different storefront templates.
JUNK_NAME_PREFIXES = (
    "trang chủ", "giỏ hàng", "tài khoản", "đăng nhập", "đăng ký", "so sánh",
    "sắp xếp", "thứ tự", "lọc giá", "bỏ hết", "xem thêm", "hãng sản xuất",
    "danh mục", "khuyến mãi", "ưu đãi", "cửa hàng", "chi nhánh", "hotline",
    "tổng đài", "chính sách", "liên hệ", "tuyển dụng", "tải app",
)

# Review/stock/discount-badge/filter-chip lines that sit between one
# product's price and the next product's name - must be excluded from
# candidacy or the next price gets paired with the wrong "name".
JUNK_NAME_RE = re.compile(
    r"^(là người đánh giá đầu tiên|xem \d+ đánh giá|-?\d+\s*%|hết hàng|"
    r"còn hàng|trả góp|góp\s*\d|chỉ còn|sản phẩm nổi bật|đánh giá)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Spec extraction: phone/tablet titles on these storefronts pack RAM/ROM
# straight into the product name (e.g. "Điện thoại Samsung Galaxy S24 Ultra
# 12GB 256GB", "iPad Gen 10 Wifi 64GB"). There's no separate structured
# spec field to scrape, so this pulls capacity back out of the title with
# best-effort regex. A field that can't be found renders as "—".
# ---------------------------------------------------------------------------
CAPACITY_RE = re.compile(r"(\d+)\s*(GB|TB)\b", re.IGNORECASE)
COLOR_HINT_RE = re.compile(
    r"\b(Xanh dương|Xanh lá|Xanh navy|Đen|Trắng|Xanh|Vàng|Bạc|Tím|Hồng|Đỏ|Cam|Titan|Xám)\b",
    re.IGNORECASE,
)


def extract_phone_tablet_specs(name):
    """Best-effort RAM/storage/color pull from a product title.

    Titles here typically carry ONE bare capacity figure (storage) or
    occasionally two (RAM then storage, larger number). Heuristic: if two
    capacity figures are found, the smaller one is RAM and the larger one
    is storage (RAM is essentially never advertised larger than storage on
    a phone/tablet listing); if only one is found, treat it as storage.
    """
    caps = [(int(v), v, u.upper()) for v, u in CAPACITY_RE.findall(name)]
    ram, rom = "—", "—"
    if len(caps) >= 2:
        caps_sorted = sorted(caps, key=lambda c: (0 if c[2] == "GB" else 1, c[0]))
        smaller, larger = caps_sorted[0], caps_sorted[-1]
        ram = f"{smaller[1]}{smaller[2]}"
        rom = f"{larger[1]}{larger[2]}"
    elif len(caps) == 1:
        rom = f"{caps[0][1]}{caps[0][2]}"

    color_match = COLOR_HINT_RE.search(name)
    color = color_match.group(1) if color_match else "—"

    return {"RAM": ram, "Dung lượng": rom, "Màu": color}


SPEC_COLUMNS = ["RAM", "Dung lượng", "Màu"]

# Accent color per retailer, used for the little badge next to each
# section heading in the email. Falls back to a neutral blue for any
# retailer not listed here (e.g. if you add one to RETAILERS).
RETAILER_ACCENT = {
    "CellphoneS": "#d70018",
    "Hoàng Hà Mobile": "#0064d2",
    "Thế Giới Di Động": "#eab600",
    "FPT Shop": "#f36f21",
}
DEFAULT_ACCENT = "#1a5fb4"


def norm(s):
    """Collapse whitespace/NBSP and normalize to NFC so diacritics compare
    equal regardless of which composed/decomposed form the page sends."""
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return unicodedata.normalize("NFC", s)


def load_last_hash(path=STATE_FILE):
    """Return the previous run's price-data hash, or None if there isn't
    one (missing/corrupt state is treated as "first run", not fatal)."""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f).get("hash")
    except (json.JSONDecodeError, OSError) as e:
        print(f"  could not read {path} ({e}) - starting with empty dedup state", file=sys.stderr)
        return None


def save_last_hash(price_hash, path=STATE_FILE):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump({"hash": price_hash, "updated": datetime.utcnow().isoformat() + "Z"}, f)


def hash_data(data):
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def fetch_page(url):
    """GET a page, verifying TLS against certifi's CA bundle explicitly.
    ALLOW_INSECURE_SSL_FALLBACK is an explicit opt-in last resort if that
    still fails."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=certifi.where())
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.SSLError as e:
        print(f"  TLS verification failed with certifi's CA bundle: {e}", file=sys.stderr)
        if not ALLOW_INSECURE_SSL_FALLBACK:
            print(
                "  Set ALLOW_INSECURE_SSL_FALLBACK=true to retry without verification "
                "as a last resort.",
                file=sys.stderr,
            )
            raise
        print("  ALLOW_INSECURE_SSL_FALLBACK=true - retrying with TLS verification disabled.", file=sys.stderr)
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        resp.raise_for_status()
        return resp.text


def _build_link_map(soup):
    """Map normalized anchor text -> absolute-able href, for every <a href>
    on the page whose text looks name-sized. Product cards on these
    storefronts almost always wrap the product name in a single <a>, so a
    name line produced by get_text("\\n") will usually match an anchor's
    own get_text() exactly. Kept separate from the line-adjacency walk so
    a lookup miss just means "no link for this item" rather than breaking
    price parsing."""
    link_map = {}
    for a in soup.find_all("a", href=True):
        a_text = norm(a.get_text(" "))
        if a_text and 8 <= len(a_text) <= 150 and a_text not in link_map:
            link_map[a_text] = a["href"]
    return link_map


def _find_link(name, link_map):
    if name in link_map:
        return link_map[name]
    # Fallback: a product name line and its wrapping anchor's text can
    # differ slightly (extra badge text inside the <a>, etc.) - try a
    # containment match rather than giving up on the link entirely.
    for a_text, href in link_map.items():
        if name in a_text or a_text in name:
            return href
    return None


def parse_listing(html, base_url, max_items=MAX_ITEMS_PER_CATEGORY):
    """
    Parse a phone/tablet category page into a list of
    {name, price, old_price, url} rows (old_price is None if not on sale;
    url is None if no matching link was found for that name).

    Product-card markup varies by retailer and changes with theme updates,
    so rather than depend on exact structure, this walks the page's
    flattened text and looks for a plausible product name line immediately
    followed - within a couple of lines - by a "X.XXX.XXX đ" price line.
    This is more resilient to markup changes than a strict DOM walk, at the
    cost of being a bit more heuristic. If a run parses 0 items, the page
    most likely renders its product grid via JavaScript rather than plain
    HTML (see module docstring) - open the URL and check with view-source,
    not just your browser, to confirm.

    Links are recovered separately (see _build_link_map/_find_link) so a
    link-matching miss never breaks name/price parsing.
    """
    soup = BeautifulSoup(html, "html.parser")
    link_map = _build_link_map(soup)
    text = soup.get_text("\n")
    lines = [norm(l) for l in text.split("\n") if norm(l)]

    items = []
    seen = set()
    i = 0
    while i < len(lines) and len(items) < max_items:
        name = lines[i]
        is_price_line = bool(PRICE_RE.search(name))
        too_short_or_long = not (8 <= len(name) <= 150)
        is_junk = name.lower().startswith(JUNK_NAME_PREFIXES) or JUNK_NAME_RE.match(name)

        if is_price_line or too_short_or_long or is_junk or name in seen:
            i += 1
            continue

        # Look ahead up to 3 lines for the first price-shaped line - that's
        # the product's price line on these storefront card layouts.
        match = None
        for j in range(i + 1, min(i + 4, len(lines))):
            m = PRICE_RE.findall(lines[j])
            if m:
                match = (j, m)
                break
            # If we hit what looks like *another* product name before
            # finding a price, this line probably wasn't a product name -
            # bail out rather than pairing it with a distant price.
            if len(lines[j]) >= 10 and not PRICE_RE.search(lines[j]):
                continue

        if not match:
            i += 1
            continue

        j, prices = match
        price = prices[0]
        old_price = prices[1] if len(prices) > 1 and prices[1] != prices[0] else None
        href = _find_link(name, link_map)
        url = urljoin(base_url, href) if href else None
        seen.add(name)
        items.append({"name": name, "price": price, "old_price": old_price, "url": url})
        i = j + 1

    return items


def fetch_category(url, max_items=MAX_ITEMS_PER_CATEGORY):
    html = fetch_page(url)
    items = parse_listing(html, base_url=url, max_items=max_items)
    for item in items:
        item["specs"] = extract_phone_tablet_specs(item["name"])
    return items


def _price_html(price, old_price):
    if old_price:
        try:
            cur = int(price.replace(".", ""))
            old = int(old_price.replace(".", ""))
            pct = round((1 - cur / old) * 100)
            discount = f" <span style='color:#cf222e'>-{pct}%</span>"
        except (ValueError, ZeroDivisionError):
            discount = ""
        return (
            f"{escape(price)} \u20ab "
            f"<span style='color:#999;text-decoration:line-through'>{escape(old_price)} \u20ab</span>"
            f"{discount}"
        )
    return f"{escape(price)} \u20ab"


def _price_text(price, old_price):
    if old_price:
        return f"{price} d (was {old_price} d)"
    return f"{price} d"


def _item_name_cell(item):
    name_html = escape(item["name"])
    if item.get("url"):
        return (
            f"<a href='{escape(item['url'])}' target='_blank' rel='noopener' "
            f"style='color:#1a5fb4;text-decoration:none;font-weight:600;'>{name_html}</a>"
        )
    return f"<span style='font-weight:600;'>{name_html}</span>"


def build_html(retailers_data, timestamp):
    sections = []
    for r in retailers_data:
        accent = RETAILER_ACCENT.get(r["retailer"], DEFAULT_ACCENT)

        if not r["items"]:
            body = (
                f"<p style='color:#777;font-size:13px;margin:8px 0 0;'>"
                f"Không lấy được sản phẩm nào ở lượt quét này. Kiểm tra trực tiếp tại "
                f"<a href='{escape(r['url'])}' style='color:{accent};'>{escape(r['url'])}</a> "
                f"(trang có thể tải sản phẩm bằng JavaScript - xem README.md).</p>"
            )
        else:
            header_cells = "".join(
                f"<th style='padding:10px 12px;text-align:left;font-size:12px;"
                f"text-transform:uppercase;letter-spacing:.03em;color:#888;'>{escape(col)}</th>"
                for col in SPEC_COLUMNS
            )
            rows = []
            for idx, item in enumerate(r["items"]):
                row_bg = "#fafafa" if idx % 2 else "#ffffff"
                specs = item.get("specs", {})
                spec_cells = "".join(
                    f"<td style='padding:10px 12px;border-bottom:1px solid #eee;"
                    f"white-space:nowrap;color:#555;font-size:13px;'>{escape(specs.get(col, '—'))}</td>"
                    for col in SPEC_COLUMNS
                )
                rows.append(
                    f"<tr style='background:{row_bg};'>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #eee;font-size:14px;'>"
                    f"{_item_name_cell(item)}</td>"
                    f"{spec_cells}"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #eee;text-align:right;"
                    f"white-space:nowrap;font-size:14px;'>{_price_html(item['price'], item['old_price'])}</td>"
                    f"</tr>"
                )
            body = f"""
<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;width:100%;font-family:Arial,Helvetica,sans-serif;">
<thead>
<tr style="background:#f5f5f5;">
<th style="padding:10px 12px;text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.03em;color:#888;">Sản phẩm</th>
{header_cells}
<th style="padding:10px 12px;text-align:right;font-size:12px;text-transform:uppercase;letter-spacing:.03em;color:#888;">Giá</th>
</tr>
</thead>
<tbody>
{''.join(rows)}
</tbody>
</table>"""

        sections.append(f"""
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;max-width:760px;margin:0 0 20px;background:#ffffff;border:1px solid #eaeaea;border-radius:10px;overflow:hidden;">
<tr>
<td style="padding:16px 18px 4px;border-left:4px solid {accent};">
<span style="display:inline-block;background:{accent};color:#fff;font-size:11px;font-weight:700;padding:3px 8px;border-radius:999px;letter-spacing:.02em;">{escape(r['retailer'])}</span>
<span style="color:#333;font-size:16px;font-weight:700;margin-left:8px;">{escape(r['category'])}</span>
<div style="color:#999;font-size:12px;margin-top:6px;">
Nguồn: <a href="{escape(r['url'])}" style="color:{accent};">{escape(r['url'])}</a>
</div>
</td>
</tr>
<tr><td style="padding:8px 18px 16px;">{body}</td></tr>
</table>""")

    return f"""\
<html>
<body style="margin:0; padding:20px; background:#f0f1f3; font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;max-width:760px;margin:0 0 20px;">
<tr><td style="padding:22px 18px;background:{DEFAULT_ACCENT};border-radius:10px;">
<h1 style="color:#fff;margin:0;font-size:20px;">📱 Giá điện thoại / tablet hôm nay</h1>
<p style="color:#dbe7fb;margin:6px 0 0;font-size:13px;">Cập nhật {escape(timestamp)}</p>
</td></tr>
</table>
{''.join(sections)}
<p style="color:#999; font-size:12px; margin-top:8px; max-width:760px;">
Đây là giá niêm yết tại từng cửa hàng riêng lẻ tại thời điểm quét, không phải
giá thị trường trung bình · Email tự động, chỉ mang tính tham khảo, không
phải lời khuyên mua hàng - vui lòng bấm vào từng sản phẩm để kiểm tra lại
giá trên website trước khi đặt hàng.
</p>
</body>
</html>"""


def build_plain_text(retailers_data, timestamp):
    lines = [f"Gia dien thoai/tablet - cap nhat {timestamp}", ""]
    for r in retailers_data:
        lines.append(f"== {r['retailer']} - {r['category']} ({r['url']}) ==")
        if not r["items"]:
            lines.append("  Could not parse any items this run.")
        else:
            for item in r["items"]:
                specs = item.get("specs", {})
                spec_str = ", ".join(f"{col}: {specs.get(col, '—')}" for col in SPEC_COLUMNS)
                price_str = _price_text(item["price"], item["old_price"])
                lines.append(f"  - {item['name']}")
                lines.append(f"    {spec_str} | Gia: {price_str}")
                if item.get("url"):
                    lines.append(f"    Link: {item['url']}")
        lines.append("")
    return "\n".join(lines)


def resolve_timestamp():
    timezone_name = os.environ.get("TIMEZONE", "Asia/Ho_Chi_Minh")
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(timezone_name))
    except Exception:
        now = datetime.now()
    return now, now.strftime("%H:%M %d/%m/%Y")


def cmd_generate():
    if os.path.exists(EMAIL_DIR):
        for f in os.listdir(EMAIL_DIR):
            os.remove(os.path.join(EMAIL_DIR, f))
    os.makedirs(EMAIL_DIR, exist_ok=True)

    retailers_data = []
    total_items = 0
    had_fetch_error = False

    for r in RETAILERS:
        print(f"Fetching {r['retailer']} - {r['category']} ({r['url']}) ...")
        try:
            items = fetch_category(r["url"])
        except requests.RequestException as e:
            print(f"  Failed to fetch {r['url']}: {e}", file=sys.stderr)
            had_fetch_error = True
            items = []

        print(f"  Parsed {len(items)} item(s).")
        if not items:
            print(
                f"  0 items parsed for {r['retailer']} - {r['category']} - the page may "
                f"render listings via JavaScript, or markup changed. Open {r['url']} "
                f"and check parse_listing().",
                file=sys.stderr,
            )
        retailers_data.append({**r, "items": items})
        total_items += len(items)

    if total_items == 0 and had_fetch_error:
        print("All retailers failed to fetch. Aborting without sending.", file=sys.stderr)
        sys.exit(1)

    price_hash = hash_data(
        [{"retailer": r["retailer"], "category": r["category"], "items": r["items"]} for r in retailers_data]
    )
    last_hash = load_last_hash()

    if total_items and SEND_ONLY_ON_CHANGE and price_hash == last_hash:
        print("Prices unchanged since last run and SEND_ONLY_ON_CHANGE=true - skipping email.")
        with open(os.path.join(EMAIL_DIR, "meta.json"), "w") as f:
            json.dump({"send": False}, f)
        return

    now, timestamp = resolve_timestamp()
    subject = f"Gia dien thoai/tablet - {now.strftime('%d/%m/%Y %H:%M')}"
    html_body = build_html(retailers_data, timestamp)
    text_body = build_plain_text(retailers_data, timestamp)

    with open(os.path.join(EMAIL_DIR, "subject.txt"), "w") as f:
        f.write(subject)
    with open(os.path.join(EMAIL_DIR, "body.html"), "w") as f:
        f.write(html_body)
    with open(os.path.join(EMAIL_DIR, "body.txt"), "w") as f:
        f.write(text_body)
    with open(os.path.join(EMAIL_DIR, "meta.json"), "w") as f:
        json.dump({"send": True, "items": total_items}, f)

    save_last_hash(price_hash)
    print(f"Generated email ({total_items} item(s) total). Saved to ./{EMAIL_DIR}/")


def cmd_send():
    sender = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = os.environ.get("PHONE_RECIPIENT")

    missing = [name for name, val in [
        ("GMAIL_ADDRESS", sender),
        ("GMAIL_APP_PASSWORD", app_password),
        ("PHONE_RECIPIENT", recipient),
    ] if not val]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    meta_path = os.path.join(EMAIL_DIR, "meta.json")
    if not os.path.exists(meta_path):
        print("No meta.json found - run 'generate' first.", file=sys.stderr)
        sys.exit(1)

    with open(meta_path) as f:
        meta = json.load(f)

    if not meta.get("send", False):
        print("Nothing to send this run (unchanged prices, or generate found no items).")
        return

    with open(os.path.join(EMAIL_DIR, "subject.txt")) as f:
        subject = f.read()
    with open(os.path.join(EMAIL_DIR, "body.html")) as f:
        html_body = f.read()
    with open(os.path.join(EMAIL_DIR, "body.txt")) as f:
        text_body = f.read()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(sender, app_password)
        server.send_message(msg)

    print(f"Sent to {recipient}!")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("generate", "send"):
        print("Usage: python phone_tablet_price_emailer.py [generate|send]", file=sys.stderr)
        sys.exit(1)
    if sys.argv[1] == "generate":
        cmd_generate()
    else:
        cmd_send()


if __name__ == "__main__":
    main()
