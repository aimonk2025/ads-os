# AdLens

AI-powered ad audit platform. Upload Google Ads, Meta Ads, and Google Analytics 4 CSV exports, get senior-level performance analysis via Claude, download client-ready HTML and PDF reports.

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=flat&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=flat&logo=flask&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-1.5+-FFF000?style=flat&logo=duckdb&logoColor=black)
![Pandas](https://img.shields.io/badge/Pandas-2.x-150458?style=flat&logo=pandas&logoColor=white)
![Claude](https://img.shields.io/badge/Claude-Code_CLI-D97757?style=flat&logo=anthropic&logoColor=white)
![Google Ads](https://img.shields.io/badge/Google_Ads-CSV-4285F4?style=flat&logo=googleads&logoColor=white)
![Meta Ads](https://img.shields.io/badge/Meta_Ads-CSV-0866FF?style=flat&logo=meta&logoColor=white)
![Google Analytics](https://img.shields.io/badge/GA4-CSV-E37400?style=flat&logo=googleanalytics&logoColor=white)

---

## First Installation

### Prerequisites

- Python 3.9 or higher
- [Claude Code CLI](https://claude.ai/code) installed and authenticated

Verify both are available:

**Windows (PowerShell or Command Prompt):**
```powershell
python --version
claude --version
```

**Mac (Terminal):**
```bash
python3 --version
claude --version
```

Claude is optional. If it is not installed, AdLens falls back to a built-in template report.

---

### Step 1 - Clone the repo

**Windows:**
```powershell
git clone https://github.com/aimonk2025/adlens.git
cd adlens
```

**Mac:**
```bash
git clone https://github.com/aimonk2025/adlens.git
cd adlens
```

---

### Step 2 - Create a virtual environment

**Windows:**
```powershell
python -m venv venv
venv\Scripts\activate
```

**Mac:**
```bash
python3 -m venv venv
source venv/bin/activate
```

You should see `(venv)` appear at the start of your terminal line confirming it is active.

---

### Step 3 - Install dependencies

**Windows:**
```powershell
pip install -r requirements.txt
```

**Mac:**
```bash
pip3 install -r requirements.txt
```

This installs everything: Flask, pandas, DuckDB, xhtml2pdf, and all other dependencies in one command. No separate installs needed.

---

### Step 4 - Start the app

**Windows:**
```powershell
python web/app.py
```

**Mac:**
```bash
python3 web/app.py
```

Open `http://localhost:5000` in your browser.

On first run, AdLens automatically creates:
- `data/adaudit.duckdb` - local database
- `uploads/` - temporary upload storage
- `reports/` and `reports/pdfs/` - generated report storage

No configuration files to edit. That is everything for setup.

---

## Everyday Use

### Starting the app

From the `adlens` folder each day:

**Windows (PowerShell or Command Prompt):**
```powershell
venv\Scripts\activate
python web/app.py
```

**Mac (Terminal):**
```bash
source venv/bin/activate
python3 web/app.py
```

Open `http://localhost:5000`.

---

### Running an audit

1. Select a client from the dropdown (or create one in Settings)
2. Go to **Audit** in the sidebar
3. Upload your Google Ads CSV and/or Meta Ads CSV
4. Optionally upload a previous period CSV for comparison deltas
5. Optionally upload a funnel CSV (leads, MQLs, SQLs, customers)
6. Click **Upload** - a cleaning report and data quality summary appear within seconds
7. Click **Run Audit** - AdLens calls Claude and builds the report (20-90 seconds)
8. View the report inline or click **Download PDF**

---

### Exporting CSVs from ad platforms

**Google Ads:**
Reports > Predefined reports > Basic > Campaigns (or Ad Groups, Keywords, Ads)
Download as CSV. AdLens accepts any column naming variation Google uses.

**Meta Ads:**
Ads Manager > Campaigns tab > Export > Export table data as CSV
Campaign level or Ad Set level exports both work.

---

### Using sample data

Tick **Use sample data** on the upload form to run a full audit without uploading any files. Useful for testing the tool or preparing a demo.

---

## App Sections

| Section | What it does |
|---------|-------------|
| **Audit** | Upload CSVs, run AI-powered audit, download HTML or PDF report |
| **Anomaly Spotter** | Flags CPL spikes >30%, ROAS drops >20%, CTR drops >25%, pacing off >15% across historical uploads |
| **Performance Narrator** | Generates an executive narrative in three tones: Executive, Detailed, or Urgent |
| **Budget Agent** | Rule-based + Claude reasoning layer for budget reallocation with confidence scores |
| **History** | Time-series campaign trends per client |
| **Settings** | Client management, currency defaults, budget rule thresholds |

---

## CSV Formats

AdLens handles messy real-world exports: encoding detection, fuzzy column matching, shorthand number expansion (1.2K, 2.5L, 1Cr), currency symbol stripping, and summary row removal.

### Google Ads

Required columns (any naming variant accepted):
```
Campaign, Impressions, Clicks, Cost, Conversions, Conversion Value
```
Optional: Ad Group, Keyword, Ad Name, Match Type, Quality Score, Ad Type, CTR, CPC

### Meta Ads

Required columns:
```
Campaign name, Impressions, Clicks, Amount spent, Results, Purchase ROAS
```
Optional: Ad Set Name, CTR, CPC

### Funnel Data (optional)

A separate CSV joining campaign names to funnel stages:
```
Campaign, Leads, MQLs, SQLs, Customers
```
AdLens auto-detects the join level (campaign, aggregate, or date) and fuzzy-matches campaign names.

### Google Analytics 4 (optional)

Export from GA4: Explore > Acquisition > Session campaign. Download as CSV.

Useful columns (any naming variant accepted):
```
Session campaign name, Sessions, Engaged sessions, Bounce rate, Average session duration, Conversions, Total revenue
```

AdLens skips the metadata rows GA4 adds at the top, joins to ad campaigns by campaign name, and adds a GA4 on-site performance table to the report showing sessions, bounce rate, and cost per session per campaign. Claude uses this data to cross-reference ad performance with on-site behavior.

---

## Granularity Support

Upload data at any level - AdLens auto-detects it:

| Level | Detected by | What happens |
|-------|-------------|--------------|
| Keyword | Has `Keyword` / `Search Term` column | Aggregates to campaign; surfaces keyword table with Quality Score |
| Ad | Has `Ad Name` / `Ad ID` + Ad Group | Aggregates to campaign; surfaces ad creative table |
| Ad Group | Has `Ad Group` / `Ad Set Name`, no keyword | Aggregates to campaign; surfaces ad group table |
| Campaign | Campaign column only | Used as-is |
| Placement | Has `Placement` / `Site` / `App` | Aggregates to campaign; surfaces placement table |

All granular rows are stored in DuckDB and included in the report and Claude's analysis.

---

## Data Storage

All data is stored locally in `data/adaudit.duckdb`. Nothing is sent to any external server. The only external call is to your local authenticated Claude Code session.

| Table | Contents |
|-------|----------|
| `clients` | Multi-client records |
| `uploads` | One row per upload with detected granularity |
| `campaigns` | Campaign-level metrics per upload |
| `granular_rows` | Keyword / ad group / ad / placement rows |
| `funnel_data` | CPL, cost per MQL, cost per SQL, cost per customer |
| `anomalies` | Detected anomalies with severity and status |
| `reports` | Saved report HTML and PDF paths |
| `budget_rules` | Per-client thresholds for the budget reallocation agent |

---

## Troubleshooting

**"Claude not found" warning in the report**
Claude Code CLI is not installed or not in your PATH. Install from [claude.ai/code](https://claude.ai/code). AdLens still generates a complete report using its built-in fallback template.


**CSV columns not recognized**
AdLens uses fuzzy column matching and accepts 100+ column name variants. Check the cleaning report shown after upload - it lists exactly which columns were found and which were renamed.

**Database errors on startup**
Delete `data/adaudit.duckdb` and restart. The schema is recreated automatically. You will lose stored history.

---

## Sample Files

```
sample_data/
  google_ads_sample.csv       campaign level
  google_adgroup_sample.csv   ad group level
  google_keyword_sample.csv   keyword level with Quality Score
  google_ad_sample.csv        ad level with Ad Type
  meta_ads_sample.csv         campaign level
  meta_adset_sample.csv       ad set level
  funnel_sample.csv           campaign-level funnel data
  google_ads_prev.csv         previous period for comparison
  meta_ads_prev.csv           previous period for comparison
  ga4_sample.csv              GA4 session/bounce/conversion data by campaign
```
