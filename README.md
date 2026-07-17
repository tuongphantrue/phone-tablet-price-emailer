# Phone / Tablet Price Emailer (runs on GitHub Actions, no local computer needed)

Emails you a digest of current phone and tablet listing prices from one or
more Vietnamese retailers (CellphoneS, Hoàng Hà Mobile, Thế Giới Di Động,
FPT Shop, ...), automatically, via GitHub's free scheduled-workflow runners.

Modeled on [tech-price-mailer](https://github.com/tuongphantrue/tech-price-mailer),
[gold-price-emailer](https://github.com/tuongphantrue/gold-price-emailer), and
[house-price-emailer](https://github.com/tuongphantrue/house-price-emailer) -
same generate/send two-phase shape, same Gmail-SMTP delivery, same
dedup-via-state-branch trick.

## Important: read this before relying on it

There's no clean, structured, frequently-updated "market price" table for
phones/tablets across retailers - this scrapes *listing prices* directly off
each retailer's own category pages. These are each store's current asking
price (often already discounted), **not** a market average and not a
verified cross-retailer comparison. Treat the email as "what page 1 of each
configured category currently shows," not an authoritative index - always
check the live page before buying.

**Confirmed status per retailer** (as of testing on 2026-07-17 - re-verify
periodically, this kind of thing changes):

| Retailer | Status | Why |
|---|---|---|
| CellphoneS | ✅ Works | Server-renders listings; parses cleanly. |
| Hoàng Hà Mobile | ✅ Works | Server-renders listings (uses **comma** thousands separators, e.g. `15,490,000 ₫` - handled). |
| FPT Shop | ❌ Blocked | Page is real and server-rendered (verified by fetching it directly) - but **GitHub Actions' runner IPs get a 403** from FPT Shop's WAF. Same request succeeds from a non-datacenter IP. |
| Thế Giới Di Động | ❌ Blocked | Also real and server-rendered - but the connection **times out** from GitHub Actions (looks like their edge is null-routing/blocklisting the datacenter IP range rather than sending an HTTP-level rejection). Its card markup also looks different enough (name/specs/price bundled into one link block) that it would likely need dedicated parsing even if the block were lifted. |

The FPT Shop / TGDD blocks are IP/network-level, not something header
tweaks can fix - the requests are being rejected before they ever reach
`parse_listing()`. Two options if you want those two working:

1. **Run outside GitHub Actions' shared IP range** - a self-hosted
   runner, a residential/Vietnam-based VPS, or a proxy in front of the
   `requests` calls. Out of scope for what this script does today.
2. **Drop them from `RETAILERS`** and rely on CellphoneS + Hoàng Hà
   Mobile, which both work reliably from GitHub Actions. Set
   `ONLY_RETAILER_KEYS=cellphones_phone,cellphones_tablet,hoanghamobile_phone,hoanghamobile_tablet`
   in the workflow to do this without editing the script.

If you want to dig further yourself, or if a *previously-working*
retailer starts reporting 0 parsed items:

1. Run `generate` once and check the logs. Every fetch now logs its HTTP
   status code and response size, and any category that parses 0 items
   gets a best-effort diagnosis printed right there - e.g. "page looks
   like a bot-challenge/consent page (matched: cloudflare + just a
   moment)" or "page looks like a JS-framework shell (Next.js/Nuxt/React
   root div)".
2. The raw HTML for any 0-item category is also saved to `debug/<key>.html`
   (set `SAVE_DEBUG_HTML=false` to turn this off). Open that file directly
   - not the live page in your browser, which will run the JS the script
   can't - to see exactly what `requests` actually received. On GitHub
   Actions, this folder is uploaded as a downloadable "debug-html-<run
   id>" artifact on every run (see the workflow's "Upload debug
   snapshots" step), so you don't need to reproduce a failure locally to
   inspect it.

The parser (`parse_listing()` in the script) matches by *text adjacency* - a
product-name-looking line immediately followed by a price line - rather
than by exact HTML structure, so it should survive minor theme/markup
changes on the retailers that do server-render. If a previously-working
retailer suddenly reports 0 parsed items with no block/SPA diagnosis, the
page layout probably changed more than that - open the saved
`debug/<key>.html` and check `parse_listing()`.

**Known limitation:** Hoàng Hà Mobile puts its crossed-out original price
on a separate line from the current price (unlike CellphoneS, which puts
both on the same line), so the discount badge/strikethrough won't show for
HHM items even when they're on sale - only the current price is captured.
Cosmetic only, the current price itself is correct.

## One-time setup (~5 minutes)

1. **Create a GitHub account** if you don't have one: <https://github.com/join>

2. **Create a new repository**
   - Click "+" (top right) -> "New repository"
   - Name it anything, e.g. `phone-tablet-price-emailer`
   - Set it to **Private** (recommended, keeps your workflow config private)
   - Click "Create repository"

3. **Upload these files** to the repo (drag-and-drop works fine via the
   GitHub web UI: "Add file" -> "Upload files"), keeping the folder structure:
   - `phone_tablet_price_emailer.py`
   - `requirements.txt`
   - `.github/workflows/send-phone-tablet-price.yml`

4. **Create a Gmail "App Password"** (your normal Gmail password won't work):
   - Turn on 2-Step Verification: <https://myaccount.google.com/signinoptions/two-step-verification>
   - Then create an app password: <https://myaccount.google.com/apppasswords>
   - Choose "Mail" as the app, copy the 16-character password it gives you.

5. **Add your secrets to the repo** (this keeps your email/password out of the code):
   - In your repo: Settings -> Secrets and variables -> Actions -> "New repository secret"
   - Add three secrets:
     - `GMAIL_ADDRESS` = your Gmail address
     - `GMAIL_APP_PASSWORD` = the 16-character app password from step 4
     - `PHONE_RECIPIENT` = the email address that should receive the price update

6. **Test it manually**
   - Go to the "Actions" tab in your repo
   - Click "Send Phone/Tablet Price Email" on the left
   - Click "Run workflow" -> "Run workflow" (green button)
   - Wait ~15-30 seconds, refresh, click into the run to see logs / confirm success
   - Check the recipient inbox for the email
   - **Check the logs for "0 items parsed" warnings** - see the caveat above
     before assuming every configured retailer is actually working.

That's it - from now on it runs automatically on the schedule below.

## Which retailers/categories are configured

Edit the `RETAILERS` list at the top of `phone_tablet_price_emailer.py`, or
override individual URLs via environment variables (add these to the
"Generate email" step in the workflow):

```
CELLPHONES_PHONE_URL       (default: https://cellphones.com.vn/mobile.html)
CELLPHONES_TABLET_URL      (default: https://cellphones.com.vn/tablet.html)
HOANGHAMOBILE_PHONE_URL    (default: https://hoanghamobile.com/dien-thoai-di-dong)
HOANGHAMOBILE_TABLET_URL   (default: https://hoanghamobile.com/may-tinh-bang)
THEGIOIDIDONG_PHONE_URL    (default: https://www.thegioididong.com/dtdd)
THEGIOIDIDONG_TABLET_URL   (default: https://www.thegioididong.com/may-tinh-bang)
FPTSHOP_PHONE_URL          (default: https://fptshop.com.vn/dien-thoai)
FPTSHOP_TABLET_URL         (default: https://fptshop.com.vn/may-tinh-bang)
MAX_ITEMS_PER_CATEGORY     (default: 12)
```

To run only a subset (e.g. while you're still debugging which retailers
actually server-render), set `ONLY_RETAILER_KEYS` to a comma-separated list
of the `key` values from `RETAILERS`, e.g.:

```
ONLY_RETAILER_KEYS: "hoanghamobile_phone,fptshop_phone"
```

## Changing the schedule

Open `.github/workflows/send-phone-tablet-price.yml` and edit this line:

```
- cron: "0 1 * * *"
```

Cron format is `minute hour day month weekday`, always in **UTC**.

- `0 1 * * *` -> once a day at 1am UTC (8am Vietnam, UTC+7) - current setting
- `0 1 * * 1` -> once a week, Monday 1am UTC
- `0 */6 * * *` -> every 6 hours

Retail listing prices don't move nearly as often as gold, so daily or
weekly is probably plenty - and keeps `SEND_ONLY_ON_CHANGE` (below) doing
useful work instead of just discarding runs.

## Only emailing on price changes

Currently `SEND_ONLY_ON_CHANGE` is `"true"` in the workflow's "Generate
email" step. With that on, `generate` hashes the freshly scraped prices,
compares against a hash saved from the last run - stored in
`state/last_price.json` on a dedicated `phone-tablet-price-state` branch the
workflow creates/updates automatically - and skips the email if nothing
changed. Set it to `"false"` if you'd rather get an email on every
scheduled run regardless.

## Email design

Each product name in the email links directly to its product page (found
via `_build_link_map`/`_find_link` in the script - it maps each anchor's
text on the page to its `href`, so a link-matching miss for one item never
breaks price parsing for the rest). Items where no matching link was found
just render as plain text instead of a link. Each retailer/category gets
its own card with a small colored badge (see `RETAILER_ACCENT` in the
script if you want to change the colors), alternating row shading, and a
discount badge on any item that's on sale. `email_preview.html` in this
repo is a static example of what the rendered email looks like.

## Notes

- The workflow needs write access to push its dedup state branch. It
  requests this itself (`permissions: contents: write` at the top of
  `send-phone-tablet-price.yml`), but some accounts/orgs override that and
  force the token to read-only regardless. If the "Persist dedup state to
  state branch" step fails with `403` / `Permission ... denied` / `exit code
  128`, go to **Settings -> Actions -> General -> Workflow permissions** in
  your repo and select **"Read and write permissions"**, then re-run the
  workflow.
- GitHub Actions free tier includes 2,000 minutes/month for private repos.
- You can also trigger it manually anytime via the "Run workflow" button.
- If the run fails, check the Actions tab -> the failed run -> logs. Common
  causes: a secret is missing/misspelled, the Gmail app password was
  revoked, or a retailer's markup changed / is JS-rendered (see the caveat
  at the top of this file).
- Always worth checking the current `robots.txt` / terms before running
  this unattended long-term, e.g.:
  - <https://cellphones.com.vn/robots.txt>
  - <https://hoanghamobile.com/robots.txt>
  - <https://www.thegioididong.com/robots.txt>
  - <https://fptshop.com.vn/robots.txt>
- This is a personal price-watch tool, not investment or purchase advice.

## Running locally instead

```
pip install -r requirements.txt
export GMAIL_ADDRESS="you@gmail.com"
export GMAIL_APP_PASSWORD="xxxx xxxx xxxx xxxx"
export PHONE_RECIPIENT="you@gmail.com"
python phone_tablet_price_emailer.py generate
python phone_tablet_price_emailer.py send
```

Schedule it yourself with cron (`crontab -e`):

```
0 1 * * * cd /path/to/phone-tablet-price-emailer && /usr/bin/python3 phone_tablet_price_emailer.py generate && /usr/bin/python3 phone_tablet_price_emailer.py send >> phone_tablet_emailer.log 2>&1
```
