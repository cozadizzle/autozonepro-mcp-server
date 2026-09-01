# autozonepro-mcp-server

> **Notice:** Open-source local tooling provided AS IS by the authors and contributors; not affiliated with ALLDATA, AutoZone, PartsGeek, or any OEM—use only with accounts you control and at your own risk (see [DISCLAIMER.md](DISCLAIMER.md)).

**License: MIT**

Local MCP server for **any** shop’s [AutoZone Pro](https://www.autozonepro.com) commercial account you are authorized to use. HTTP-first catalog/session client: **commercial cost and list price**, read-only **credit snapshot** (balance / overdue / available credit), and a **bounded invoice/receipt scan**.

## What it does

| Tool | Purpose |
|------|---------|
| `get_session_status` | Auth, shop, store, sticky vehicle |
| `list_vehicles` | Garage / VIN-saved vehicles |
| `lookup_plate` | Plate → YMME + VIN + ACES; `bind=True` for parts |
| `lookup_vin` | VIN → YMME + ACES ids (binds after decode) |
| `set_vehicle_ymme` | Bind from year/make/model[/engine] |
| `bind_garage_vehicle` | Bind a saved garage vehicle |
| `get_bound_vehicle` | In-process bound ACES / name |
| `search_parts` | Keyword/PN search; **cost + list_price**; `expect_vehicle` mismatch guard |
| `list_group_products` | Part-group catalog (pads, rotors, oil filter, motor oil) |
| `get_prices` | Refresh cost/list/core/store qty |
| `list_categories` | Top-level categories |
| `get_credit_snapshot` | Read-only **balance**, **overdue / past-due**, **available credit** (one small call) |
| `scan_invoices` | Bounded invoices/receipts (`limit` max 15, last `days`) — id, date, amount, type |
| `get_invoice` | Download one commercial invoice/return **PDF** and parse line items (list/cost/core) |
| `search_invoices` | Find tickets by invoice #, PO, part number, vehicle, or text |
| `tally_invoices` | Sum tickets by day/month/year (includes cores, returns, payments) |
| `get_annual_purchases` | Official AZ monthly sales for a calendar year (**excludes cores**) |
| `get_shop_profile` / `set_shop_profile` | Local garage name, AutoZone store #, address (first-run) |
| `import_browser_cookies` | **Optional local auth** from a browser you name |

**Quote rule:** when building a job quote, always show **cost and list** side-by-side. Cost = what the shop pays; list = street/list.

**Account (read-only):** `get_credit_snapshot` for current balance, overdue/past-due, and available credit. `scan_invoices(limit=15, days=90)` for recent invoices and payment receipts. `get_invoice(invoice_id)` fetches the PDF and parses parts/qty/cost/list. `search_invoices` / `tally_invoices` cover lookup and monthly/yearly totals. Do **not** pay bills or save payment methods through this server. Do **not** commit downloaded PDFs or `~/.cache/azpro-invoices`.

## Requirements

- Python ≥ 3.10  
- AutoZone Pro commercial access you are authorized to use  
- Optional: `browser-cookie3` for one-shot cookie export  

## Install

```bash
git clone <this-repo-url> autozonepro-mcp-server
cd autozonepro-mcp-server
pip install -e .
pip install browser-cookie3   # for cookie import
```

```toml
# ~/.grok/config.toml
[mcp_servers.azpro]
command = "azpro-mcp"
enabled = true
```

```bash
hermes mcp add azpro --command azpro-mcp
```

## First run (shop profile)

On the first interactive start (`azpro-mcp` in a terminal, or `azpro-mcp --setup`), you are prompted for **garage name**, **AutoZone store number**, and **address**. If you are already logged in, suggestions are filled from AutoZone Pro’s shop/store header — confirm or edit them.

When the server is launched over MCP stdio (no TTY), the agent should ask you the same questions and call `set_shop_profile`. Profile is stored only at:

```text
~/.config/autozonepro/shop.json
```

Override with `AZPRO_SHOP_FILE`. This is **not** AutoZone login and is **not** committed or uploaded.

**Updates do not wipe a shop.** `git pull` / `pip install -e .` only update code in the clone. Cookies stay at `~/.config/autozonepro_cookies.json`. The shop profile stays at `~/.config/autozonepro/shop.json`. `set_shop_profile` **merges** — empty fields do not clear a name/address already saved. Re-run `--setup` only when you *want* to edit the prompt.

```bash
azpro-mcp --setup
```

## Authentication (local cookies)

**Do not commit cookie files.** Default path:

```text
~/.config/autozonepro_cookies.json
```

### Recommended: AI-assisted browser import

1. Log into https://www.autozonepro.com in a browser on **this computer**.
2. When asked, name that browser (`brave`, `chrome`, `firefox`, …).
3. Agent runs `import_browser_cookies(browser="brave")` **only after you say yes**.
4. Session JWT is obtained via AZ Pro session APIs using those cookies—still local config + HTTPS to AutoZone.

### CLI

```bash
python scripts/export_browser_cookies.py --browser brave
```

### Env

| Variable | Meaning |
|----------|---------|
| `AZPRO_COOKIES_FILE` | Cookie JSON path (default above) |
| `AZPRO_COOKIES` | Inline JSON (prefer file) |
| `AZPRO_SHOP_FILE` | Shop profile JSON (default `~/.config/autozonepro/shop.json`) |

## Data handling

- Cookie export is **local** and **opt-in**.
- Traffic goes to AutoZone Pro APIs for your commercial account.
- Authors do not collect or resell your credentials.
- Full terms: **[DISCLAIMER.md](DISCLAIMER.md)**.

## Pair with labor + RO skill

- **alldata** MCP → labor hours  
- **shop-quote** skill → dual-price RO templates  

## Development

```bash
pytest -q
```

## License

MIT — see [LICENSE](LICENSE).
