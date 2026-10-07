# Perceptric SEO Research

A simple in-house alternative to Semrush for the Perceptric team. It pulls data from the DataForSEO API, and you only pay for the searches you run.

## What it does

| Tab | What you get |
|---|---|
| Keyword overview | Paste up to 100 keywords and get volume, keyword difficulty (KD), CPC, competition, intent and a 12-month trend |
| Keyword ideas | Enter a seed keyword and get up to 1,000 related ideas, with filters for volume, KD, intent and words like "best" or "vs" |
| Domain overview | Enter any domain and get organic keywords, estimated traffic, top-3 rankings, referring domains, top ranking keywords and organic competitors |
| SERP check | See Google's top 10 for any keyword, and whether an AI Overview shows up |

Every table has a **Download CSV** button. **Summarize with Claude** (optional) turns a table into plain-language takeaways and opportunities.

Repeat searches within 24 hours are cached, so they're free. The sidebar shows how much you've spent in your session.

## 1. Run it on your computer

You need Python 3.10 or newer.

```bash
cd perceptric-seo-app
python3 -m venv .venv
source .venv/bin/activate        # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Open `.streamlit/secrets.toml` and fill in:

- `DFS_LOGIN`: your DataForSEO account email
- `DFS_PASSWORD`: your DataForSEO **API password** (from the API Access page, not your login password)
- `APP_PASSWORD`: a shared password your team will use to log in
- `ANTHROPIC_API_KEY` (optional): turns on the Claude summaries

Then start the app:

```bash
streamlit run app.py
```

It opens at http://localhost:8501.

## 2. Share it with the team

Pick one of these:

- **Streamlit Community Cloud (free):** push this folder to a **private** GitHub repo, create an app at share.streamlit.io, paste the contents of your `secrets.toml` into the app's Secrets settings, and share the link. Check their current rules on private apps and viewer limits.
- **Render or Railway (about $5–10/month):** create a new web service from the repo with the start command `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`, then add each secret as an environment variable.

Everyone logs in with the shared `APP_PASSWORD`.

## 3. Keep it safe

- **Never commit `secrets.toml`.** It's already in `.gitignore`.
- **Set a spending limit** in your DataForSEO dashboard, so a mistake can't drain your balance.
- **If a password is ever shared by accident** (in chat, email or Slack), generate a new one right away and update your secrets.
- Change `APP_PASSWORD` when someone leaves the team.

## Rough costs per search

These are estimates. Check DataForSEO's pricing page for current rates.

| Action | Rough cost |
|---|---|
| Keyword overview (up to 100 keywords) | about $0.01–0.03 |
| Keyword ideas (100 results) | about $0.02–0.03 |
| Domain overview (4 requests) | about $0.10–0.15 |
| SERP check | under $0.01 |

For a team doing research for 5 clients, expect roughly $20–60 a month.

## Changing things

- **Add a country:** add it to `LOCATIONS` in `app.py` using its DataForSEO location code.
- **Change the Claude model:** set `CLAUDE_MODEL` in your secrets.
