# TrendPulse – Deployment Guide
## GitHub → Railway (step by step)

---

## Step 1 — Test locally first (5 min)

Open Terminal, navigate to this folder, then run:

```bash
cd /path/to/trend-tracker

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate        # Mac/Linux
# venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py
```

Open http://localhost:8000 in your browser.

> **Note:** The first load fetches live data from Google Trends — it takes ~60 seconds
> while it works through batches. Subsequent loads are instant (cached).

---

## Step 2 — Create a GitHub repo (3 min)

1. Go to https://github.com/new
2. Name it `trend-pulse` (or anything you like)
3. Set it to **Private** (recommended — keeps your keyword list private)
4. Click **Create repository**
5. GitHub will show you commands. In Terminal (inside your `trend-tracker` folder):

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/trend-pulse.git
git push -u origin main
```

Replace `YOUR_USERNAME` with your actual GitHub username.

---

## Step 3 — Deploy to Railway (5 min)

1. Go to https://railway.app and sign in with GitHub
2. Click **New Project**
3. Click **Deploy from GitHub repo**
4. Select `trend-pulse` from the list
5. Railway auto-detects Python and starts building

**That's it.** Railway reads `railway.toml` automatically.

After ~2 minutes you'll see a green **Active** status.

---

## Step 4 — Get your public URL

1. In Railway, click your service (the box that appeared)
2. Click the **Settings** tab
3. Under **Networking**, click **Generate Domain**
4. Railway gives you a URL like `trend-pulse-production.up.railway.app`

Open that URL — your live app is deployed. ✅

---

## Step 5 — Customize your keyword list (ongoing)

Open `app.py` and edit the `TRACKED_KEYWORDS` list at the top:

```python
TRACKED_KEYWORDS = [
    "Your New Keyword",
    "Another Product",
    # ... up to ~50 keywords works well
]
```

Also update `CATEGORY_MAP` to assign each new keyword a category.

Then push the change:

```bash
git add app.py
git commit -m "Update keyword list"
git push
```

Railway automatically redeploys within ~90 seconds.

---

## Troubleshooting

### App loads but shows no data / spinner forever
- Check Railway logs: click your service → **Logs** tab
- Google sometimes rate-limits. Wait 10 min and hit the Refresh button in the app.
- If it keeps failing, reduce batch frequency by increasing the sleep in `fetch_all_trends()`:
  ```python
  time.sleep(5)  # increase from 3 to 5
  ```

### "Proxy error" in logs
Google Trends can block cloud datacenter IPs. Solutions:
1. Use SerpAPI instead of pytrends (drop-in swap, see bottom of app.py)
2. Deploy to a residential proxy or home server
3. Add the `SCRAPER_API_KEY` env var and switch to ScraperAPI

### How to force a manual data refresh
Hit the **Refresh** button in the top-right of the app, or call:
```
POST https://your-app.up.railway.app/api/refresh
```

### Check cache status
```
GET https://your-app.up.railway.app/api/status
```
Returns when data was last fetched and how many keywords are cached.

---

## Upgrading to SerpAPI (if pytrends gets blocked)

1. Sign up at https://serpapi.com (free tier: 100 searches/month)
2. Get your API key
3. In Railway: Settings → Variables → add `SERPAPI_KEY=your_key_here`
4. In `app.py`, replace `fetch_all_trends()` with the SerpAPI version:

```python
import requests

def fetch_trends_for_keyword_serpapi(kw: str, api_key: str) -> list[float]:
    params = {
        "engine": "google_trends",
        "q": kw,
        "date": "today 12-m",
        "geo": "US",
        "api_key": api_key,
    }
    r = requests.get("https://serpapi.com/search", params=params)
    data = r.json()
    timeline = data.get("interest_over_time", {}).get("timeline_data", [])
    return [item["values"][0]["extracted_value"] for item in timeline[-12:]]
```

---

## File structure recap

```
trend-tracker/
├── app.py              ← Flask backend + pytrends logic
├── requirements.txt    ← Python dependencies
├── railway.toml        ← Railway deployment config
├── .gitignore
├── DEPLOY.md           ← This file
├── cache/              ← Auto-created at runtime (gitignored)
│   └── trends.json
└── static/
    └── index.html      ← Frontend app
```
