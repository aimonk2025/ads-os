# Ads OS

AI-powered ad audit and agency management platform. Upload Google Ads, Meta Ads, and Google Analytics 4 CSV exports, get senior-level performance analysis via Claude, and manage all clients from a single dashboard.

> No API keys required. Ads OS uses your local Claude Code CLI session - just authenticate once and it works.

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=flat&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat&logo=flask&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-1.5+-FFF000?style=flat&logo=duckdb&logoColor=black)
![Pandas](https://img.shields.io/badge/Pandas-2.x-150458?style=flat&logo=pandas&logoColor=white)
![Claude](https://img.shields.io/badge/Claude-Code_CLI-D97757?style=flat&logo=anthropic&logoColor=white)
![Google Ads](https://img.shields.io/badge/Google_Ads-CSV-4285F4?style=flat&logo=googleads&logoColor=white)
![Meta Ads](https://img.shields.io/badge/Meta_Ads-CSV-0866FF?style=flat&logo=meta&logoColor=white)
![Google Analytics](https://img.shields.io/badge/GA4-CSV-E37400?style=flat&logo=googleanalytics&logoColor=white)

---

## Starting the App

From the `ads-os` folder in PowerShell:

```powershell
venv\Scripts\activate
python web/app.py
```

Then open `http://localhost:5000` in your browser.

---

## First Installation

### Prerequisites

- Python 3.9 or higher
- [Claude Code CLI](https://claude.ai/code) installed and authenticated

Verify both:

```powershell
python --version
claude --version
```

Claude is optional. If not installed, Ads OS falls back to a built-in template report.

---

### Step 1 - Clone the repo

```powershell
git clone https://github.com/aimonk2025/ads-os.git
cd ads-os
```

---

### Step 2 - Create a virtual environment

```powershell
python -m venv venv
venv\Scripts\activate
```

You should see `(venv)` at the start of your terminal line.

---

### Step 3 - Install dependencies

```powershell
pip install -r requirements.txt
```

This installs everything: Flask, pandas, DuckDB, xhtml2pdf, requests, and all other dependencies.

---

### Step 4 - Start the app

```powershell
python web/app.py
```

Open `http://localhost:5000` in your browser.

On first run, Ads OS automatically creates:
- `data/adaudit.duckdb` - local database (all data stored here)
- `uploads/` - temporary upload storage
- `reports/` and `reports/pdfs/` - generated report files

No configuration files to edit.

---

## App Sections

| Section | What it does |
|---------|-------------|
| **Overview** | Multi-client health dashboard. ROAS, spend, anomaly count, budget health, and onboarding setup progress per client. "Run All Clients" triggers bulk reporting. |
| **Executive Dashboard** | KPI cards (ROAS, MER, CPA, Total Spend, Conversions), P&L waterfall, Revenue vs Spend trend chart, channel allocation split. Date range: 7 / 30 / 90 days / all. |
| **Revenue Forecast** | WMA-based forward projection per campaign. Projected spend, ROAS, conversions, trend direction. Claude narrative. Seasonality adjustment. 7 / 30 / 60 day windows. |
| **Structured Audit** | 50+ checkpoints across 6 categories (Tracking, Architecture, Ad Set Config, Creative, Cost Diagnostics, Account Health). Scored 0-100. Action items auto-pushed to Action Plan. Claude executive summary. |
| **Pixel Health** | Meta Pixel and CAPI health monitor via Meta Graph API. 11 checks: event tracking, deduplication ratio, event match quality, CAPI freshness. Scored 0-100. |
| **Audit** | Upload CSVs, run AI-powered audit, download HTML or PDF report. Supports Google Ads, Meta Ads, GA4, Funnel data, and compare periods. |
| **Anomaly Spotter** | Flags CPL spikes, ROAS drops, CTR drops, spend pacing anomalies vs rolling median baseline. Open / Acknowledged / Resolved status tracking. |
| **Narrator** | Executive narrative in three tones: Executive, Detailed, or Urgent. |
| **Budget Agent** | Rule-based + Claude reasoning for budget reallocation with confidence scores. Campaign-type overrides supported. |
| **Action Plan** | Consolidated to-do from audit, budget, anomaly, and structured audit sources. Filter by priority and source. Mark done, add notes. |
| **Context** | Per-client business context: business type, goals, audience, attribution model, campaign type tags, per-campaign targets. |
| **History** | Time-series campaign trends. Multi-period comparison table (up to 4 uploads side by side). |
| **Reports** | Viewer for all saved HTML and PDF reports per client. |
| **Settings** | Client management, budget rules, campaign type overrides, pixel credentials, agency branding. |

---

## KPI Alert System

Alerts are triggered automatically on every CSV upload. No manual action required.

### Alert types

| Alert | Trigger |
|-------|---------|
| ROAS Below Target | Campaign ROAS below client's Min ROAS floor |
| Zero Conversions | Significant spend with zero recorded conversions |
| CPA Spike | CPA exceeds CPL ceiling or up >30% vs prior period |
| CTR Drop | CTR down >20% vs prior upload |
| CVR Drop | Conversion rate down >25% vs prior upload |
| Spend Overpace | Spend up >40% vs prior upload |
| Spend Underpace | Spend down >40% vs prior upload |

### Severity levels

| Severity | Condition |
|----------|-----------|
| High | ROAS miss >30%, CPA spike >50%, CTR/CVR drop >40%, spend overpace >30% |
| Medium | All other threshold breaches |

### Notification bell

The bell icon in the app header shows an unread count badge. Click it to open the alerts panel showing all new alerts across all clients, sorted by severity then date. Dismiss individually or bulk-dismiss per client.

Thresholds (Min ROAS, CPL ceiling) are configured per client in **Settings > Budget Rules**.

---

## Bulk Client Reporting

Run all clients in one click from the Overview page.

1. Click **Run All Clients** on the Overview section header
2. A progress modal shows "Running [Client Name]... (3 of 8)" as each client processes
3. When complete, a multi-client summary HTML report is generated and linked
4. The summary table shows: Client, Period, Blended ROAS, Total Spend, Open Anomalies, Budget Health (Good / Warning / Critical), and a link to each client's full audit report

Each client uses its most recent upload. Clients run sequentially to avoid Claude CLI conflicts.

---

## Executive Dashboard

Available under **Dashboard** in the sidebar. Per-client, date-range filtered.

**KPI Cards:** Total Spend, Total Revenue, Blended ROAS, MER, Total Conversions, CPA

**Charts:**
- P&L Waterfall: Spend vs Conversions vs Revenue
- Revenue vs Spend trend line across historical upload periods
- Channel allocation: Meta vs Google spend + ROAS side by side

**Date ranges:** Last 7 / 30 / 90 days or All time

All data comes from your existing uploaded CSVs - no re-upload needed.

---

## Revenue Forecasting

Available under **Forecast** in the sidebar.

**Model:** Weighted Moving Average (WMA) on ROAS, spend, CPL, conversions per campaign across historical upload periods. More recent periods are weighted higher.

**Seasonality:** If the same calendar month exists from a prior year, a seasonal adjustment factor is applied (clamped between 0.5x and 2.0x).

**Output per campaign:** Projected spend, projected ROAS, projected conversions, trend direction (up / flat / down)

**Account rollup:** Total projected spend, blended ROAS, estimated conversions

**Claude narrative:** 2-3 paragraph forward-looking interpretation of projections

**Forecast window:** 7 / 30 / 60 days (selector at top)

Requires at least 2 historical upload periods to generate projections. 3+ periods recommended.

---

## Structured Audit

Available under **Structured Audit** in the sidebar. Runs from existing uploaded data - no re-upload required.

### 6 categories, 50+ checks

| Category | What is checked |
|----------|----------------|
| Tracking Foundation | Zero-conversion campaigns, missing conversion data, conversion value gaps |
| Campaign Architecture | Objective mix, budget concentration risk, naming consistency, campaign count |
| Ad Set / Ad Group Config | Learning phase signals (low impressions), bid strategy mix, audience signals |
| Creative Performance | CTR decline across consecutive uploads (fatigue), format diversity, CTA patterns |
| Cost Diagnostics | CPM outliers, CPC vs benchmark, CPA vs client target, ROAS gaps vs target |
| Account Health | Zero-spend campaigns, spend concentration, impressions-but-no-clicks campaigns |

Each check: **Pass / Warning / Fail** + one-line reason

**Scoring:** Category score (0-100) + overall account health score (weighted)

**Output:** Recommendations table (Priority, Campaign, Finding, Suggested Action) + Claude executive summary paragraph

Action items from the structured audit are automatically pushed to the Action Plan.

---

## Meta Pixel Health Monitor

Available as a sub-section of Structured Audit. Requires Meta Pixel ID and access token (entered in Settings per client).

### 11 health checks

| Check | What it measures |
|-------|-----------------|
| Pixel Active | Pixel is live and firing |
| PageView Events | PageView fires present and recent |
| Purchase Events | Purchase events tracked |
| AddToCart Events | AddToCart events tracked |
| ViewContent Events | ViewContent events tracked |
| InitiateCheckout Events | InitiateCheckout events tracked |
| CAPI Events | Server-side events received |
| CAPI Freshness | Last server event within 24 hours |
| Event Match Quality | EMQ scores per event type |
| Deduplication Ratio | Browser vs server event overlap |
| Domain Verification | Pixel verified against domain |

Each check: **Good / Warning / Critical** + reason + score

**Overall pixel health score:** 0-100 with label (Healthy / Warning / Critical)

---

## Client Onboarding Wizard

Triggered when you create a new client, or launched via the **Setup** button on the Overview table.

### 6 steps

| Step | What is configured |
|------|--------------------|
| 1. Basic Info | Client name, industry, business type, currency |
| 2. Campaign Goals | ROAS target, CPL target, campaign tags, goals notes |
| 3. Platform Setup | Which platforms: Google Ads / Meta Ads / GA4 |
| 4. Data Collection | Step-by-step export guide for each selected platform |
| 5. Budget Rules | ROAS floors and CPL ceilings for the Budget Agent and KPI Alerts |
| 6. First Upload | Links to the Audit section + Generate Client Brief button |

### Onboarding progress

- Progress bar (%) shown per client in the Overview table under the **Setup** column
- Green checkmark when all 6 steps are complete
- **Setup** button opens the wizard at any time to complete remaining steps

### Generate Client Brief

On Step 6, click **Generate Brief**. Claude produces a 1-page HTML brief with:
- Client overview
- Campaign objectives and targets
- Platform strategy
- KPI benchmarks table
- Tracking setup summary
- Next steps

Brief is saved to `reports/` and viewable from the Reports section.

---

## Running an Audit

1. Select a client from the dropdown (or create one in Settings)
2. Go to **Audit** in the sidebar
3. Drop any CSV onto the universal drop zone - platform is auto-detected
4. Or drop files onto the specific Google Ads / Meta Ads / GA4 / Funnel zones
5. Set a Period Label and Start / End dates (used for historical tracking)
6. Optionally enable **Compare Periods** and upload a previous period CSV
7. Click **Upload and Analyze** - a cleaning report and data quality summary appear within seconds
8. Claude runs the audit and builds the report (20-90 seconds)
9. View the report inline or click **Download PDF**

KPI Alerts run automatically after every upload.

---

## Importing Historical Data

To backfill months of data for trend analysis and forecasting:

1. Click **Import History** on the Audit page
2. Click **+ Add Period** for each month or quarter
3. Set a label (e.g. `Jan 2026`), start date, and end date
4. Drop CSV files for each period - platform is auto-detected
5. Click **Import All Periods**

After import, trend charts in History show the full arc. The anomaly detector and forecasting engine both use this historical baseline.

---

## Exporting CSVs from Ad Platforms

**Google Ads:**
Reports > Predefined reports > Basic > Campaigns
Download as CSV. Include: Impressions, Clicks, Cost, Conversions, Conversion Value.

**Meta Ads:**
Ads Manager > Campaigns tab > Export > Export table data as CSV
Campaign level or Ad Set level both work.

**GA4:**
Explore > Acquisition > Traffic acquisition
Set dimension: Session campaign. Download as CSV.
Include: Sessions, Engaged sessions, Bounce rate, Conversions, Total revenue.

---

## Platform Auto-Detection

Ads OS identifies the platform of any CSV before upload by scanning the first 2KB of column headers.

| Platform | Key signals |
|----------|------------|
| Google Ads | `conversion value`, `quality score`, `search impression share`, `match type` |
| Meta Ads | `purchase roas`, `amount spent`, `ad set name`, `results` |
| GA4 | `# Google Analytics 4` comment, `session campaign name`, `engaged sessions` |
| Funnel | `leads`, `mqls`, `sqls`, `customers` |

Detection includes a confidence score. If below 20%, the file is marked unknown and you can manually assign it.

---

## Anomaly Detection

Every upload is compared against a rolling median baseline from the last 7 historical uploads.

### Thresholds

| Sensitivity | CPL/CAC warning | CPL/CAC critical | ROAS warning | ROAS critical | CTR warning | CTR critical |
|------------|----------------|-----------------|-------------|--------------|------------|-------------|
| Low | +50% | +100% | -35% | -60% | -40% | -70% |
| Medium (default) | +30% | +60% | -20% | -40% | -25% | -50% |
| High | +15% | +30% | -10% | -20% | -15% | -30% |

Spend pacing: warning at 15% off expected pace, critical at 30%.
Creative fatigue: CTR declining for 3+ consecutive uploads with total drop over 10%.

Campaigns tagged `brand` or `test` are excluded. Seasonal patterns (same calendar month, prior year within 25%) are automatically suppressed.

**Target-based anomalies:** If per-campaign targets are set in Context, campaigns that miss their own target are flagged independently of the rolling median.

**Status tracking:** Each anomaly moves through Open / Acknowledged / Resolved in the Anomaly Spotter panel.

---

## Data Storage

All data is stored locally in `data/adaudit.duckdb`. Nothing is sent to any external server. The only external calls are to your local Claude Code CLI and (optionally) the Meta Graph API for Pixel Health.

| Table | Contents |
|-------|----------|
| `clients` | Client records with currency, context, pixel credentials |
| `uploads` | One row per upload with period dates, platform list, granularity |
| `campaigns` | Campaign-level metrics per upload |
| `granular_rows` | Keyword / ad group / ad / placement rows |
| `funnel_data` | CPL, cost per MQL, SQL, customer |
| `anomalies` | Detected anomalies with severity, type, Open/Acknowledged/Resolved status |
| `kpi_alerts` | KPI threshold alerts with severity, campaign, type, status |
| `action_items` | Action Plan items with priority, source, completion state |
| `reports` | Saved report HTML and PDF paths |
| `budget_rules` | Per-client thresholds for budget agent and KPI alerts |
| `forecasts` | WMA forecast results per client and horizon |
| `structured_audits` | 50+ checkpoint results with category scores |
| `pixel_health_reports` | Meta Pixel health check results |
| `competitors` | Per-client competitor brand list |
| `competitor_ads` | Scraped and Claude-analyzed ads per competitor |
| `onboarding_status` | Per-client wizard step completion tracking |

---

## Budget Agent

Runs two layers:

1. **Rule-based** (`src/budget_agent.py`) - hard rules enforced regardless of Claude availability. Violations computed from per-client ROAS and CPL thresholds with optional campaign-type overrides.
2. **Claude reasoning layer** - Claude receives pre-computed violations + campaign metrics and returns confidence scores and rationale per recommendation.

If Claude is unavailable, the rule-based layer generates the report with a fallback explanation.

### Budget rules (configured per client in Settings)

| Rule | Default | Description |
|------|---------|-------------|
| Google min ROAS | 2.0x | Campaigns below this are flagged for decrease or pause |
| Meta min ROAS | 2.0x | Same for Meta |
| Google min CPL | none | Optional CPL ceiling |
| Meta min CPL | none | Optional CPL ceiling |
| Max shift per cycle | 20% | Budget cannot shift more than this in a single report |
| Campaign type overrides | none | Custom ROAS/CPL floors by campaign name keyword |

---

## Performance Narrative

Generates a structured weekly summary in three tones via `src/narrator.py`.

| Tone | Audience | Style |
|------|----------|-------|
| Executive | C-suite, clients | High-level, outcome-focused |
| Detailed | Agency teams, analysts | Full metric breakdown, trend context |
| Urgent | Media buyers | Action-first, flags bleeding campaigns immediately |

Claude always outputs four sections: This Week in Numbers, What Worked, What Needs Attention, Recommended Actions for Next Week.

---

## Morning Brief

**Web UI:** Morning Brief panel in the app sidebar. Shows anomaly summary, spend pacing, and threshold breaches since last upload.

**CLI:**

```powershell
python brief.py
python brief.py --client-id 1
python brief.py --watch --interval 300
```

Requires `python web/app.py` running in another terminal window.

---

## Report Branding

Agency logo and name are set in **Settings > Branding**. Once set, every generated report header shows the agency logo and name alongside the Ads OS badge. Applies to all HTML, PDF, and multi-client summary reports.

---

## CSV Export

Every data table includes a **Download CSV** button. Available on:

- Campaign table in the Audit report
- Anomaly list in Anomaly Spotter
- Historical comparison table in History
- Budget reallocation actions table

---

## PDF Reports

Generated using `xhtml2pdf`. Saved to `reports/pdfs/` and linked from the Reports viewer. PDFs mirror the HTML report layout including KPI cards, tables, and Claude analysis sections.

---

## Manual Column Mapping

If your CSV uses non-standard column names:

1. Drop a CSV onto any upload zone
2. A **Map Columns** button appears below the file name
3. Click to open the mapping modal
4. Review or override fuzzy-matched columns
5. Unmatched required fields are highlighted
6. Click **Apply**

---

## CSV Formats

### Google Ads

Required columns (any naming variant accepted):
```
Campaign, Impressions, Clicks, Cost, Conversions, Conversion Value
```
Optional: Ad Group, Keyword, Ad Name, Match Type, Quality Score, CTR, CPC

### Meta Ads

Required columns:
```
Campaign name, Impressions, Clicks, Amount spent, Results, Purchase ROAS
```

### Funnel Data (optional)

```
Campaign, Leads, MQLs, SQLs, Customers
```

### Google Analytics 4 (optional)

```
Session campaign name, Sessions, Engaged sessions, Bounce rate,
Average session duration, Conversions, Total revenue
```

---

## Troubleshooting

**"Claude not found" warning in the report**
Claude Code CLI is not installed or not in PATH. Install from [claude.ai/code](https://claude.ai/code). Ads OS still generates a complete report using its built-in fallback template.

**CSV columns not recognized**
Check the cleaning report shown after upload - it lists every column found and which were renamed. Use the Manual Column Mapping modal to fix anything the fuzzy matcher missed.

**Platform detected incorrectly**
Drop the file onto the correct specific zone (Google Ads / Meta Ads / GA4 / Funnel) to override detection.

**Date column not normalizing correctly**
Check the cleaning report - it lists every date normalization applied. For ambiguous DD/MM vs MM/DD formats, Ads OS scans the whole column to determine ordering and defaults to day-first if ambiguous.

**Garbled characters in reports**
Verify you are running Python 3.9+ and that `src/claude_client.py` and `src/budget_agent.py` have `encoding="utf-8"` on their subprocess calls.

**Forecast shows no projections**
At least 2 historical upload periods are required. Import historical data via **Import History** on the Audit page.

**Pixel Health returns errors**
Verify the Meta Pixel ID and access token are correctly entered in Settings for this client. The token requires `ads_read` and `pixel` permissions on the Meta app.

**Database errors on startup**
Delete `data/adaudit.duckdb` and restart. The schema is recreated automatically. You will lose stored history and reports.

**Report CSS not loading**
Ensure the Flask app is running (not opening the HTML file directly from disk) and that `web/static/report.css` exists.

---

## Source Files

```
src/
  db.py                DuckDB schema, all CRUD helpers
  loader.py            Google Ads and Meta Ads CSV loading
  ga_loader.py         GA4 CSV loading and merge
  funnel_loader.py     Funnel CSV loading
  cleaner.py           Fuzzy column matching, encoding fix, number normalization
  detector.py          Platform auto-detection from column headers
  calculator.py        Metric computation (ROAS, CAC, CTR, CPL)
  context.py           Per-client business context formatting
  claude_client.py     Claude CLI subprocess wrapper
  renderer.py          Markdown-to-HTML rendering pipeline
  anomaly_detector.py  Rolling median anomaly detection
  narrator.py          Performance narrative generation
  budget_agent.py      Rule-based + Claude budget reallocation
  forecaster.py        WMA forecasting engine
  structured_audit.py  50+ checkpoint structured audit
  pixel_monitor.py     Meta Pixel and CAPI health monitor
  alert_engine.py      KPI alert threshold checks and persistence
  bulk_reporter.py     Multi-client bulk report runner
  onboarding.py        Client onboarding wizard logic and brief generation
  dashboard.py         Executive dashboard data queries
  copilot.py           AI copilot chat context builder and Claude call
  bulk_splitter.py     Bulk CSV file splitting by client column
  granularity.py       Sub-campaign granularity detection and insights
  templates/
    report.html        Jinja2 audit report template

web/
  app.py               Flask routes and API endpoints
  templates/
    app.html           Single-page application UI
  static/
    report.css         Shared report stylesheet
```
