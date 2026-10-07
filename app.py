"""
Perceptric SEO Research — a simple in-house Semrush alternative.
Data: DataForSEO API (pay per request). Optional AI summaries: Claude API.
Run locally:  streamlit run app.py
"""
import json
import os

import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Perceptric SEO Research", page_icon="🔎", layout="wide")

API = "https://api.dataforseo.com/v3"

LOCATIONS = {
    "United States": 2840, "United Kingdom": 2826, "Canada": 2124, "Australia": 2036,
    "Singapore": 2702, "Vietnam": 2704, "Germany": 2276, "France": 2250, "India": 2356,
}
LANGUAGES = {"English": "en", "Vietnamese": "vi", "German": "de", "French": "fr"}


# ───────────────────────── helpers ─────────────────────────
def secret(name, default=None):
    """Read a secret from .streamlit/secrets.toml or an environment variable."""
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name, default)


def dig(d, *keys, default=None):
    """Safely read nested keys: dig(item, "keyword_info", "cpc")."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def _post(path, payload_json):
    """One DataForSEO request. Cached for 24h so the same search never costs twice."""
    r = requests.post(
        f"{API}/{path}",
        auth=(secret("DFS_LOGIN"), secret("DFS_PASSWORD")),
        json=json.loads(payload_json),
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def call(path, payload):
    """Call an endpoint and return the task's result list (or None on error)."""
    key = json.dumps([payload], sort_keys=True)
    try:
        data = _post(path, key)
    except requests.HTTPError as e:
        st.error(f"DataForSEO returned an error: {e}")
        return None
    except Exception as e:
        st.error(f"Could not reach DataForSEO: {e}")
        return None
    if data.get("status_code") != 20000:
        st.error(f"DataForSEO: {data.get('status_message')}")
        return None
    task = (data.get("tasks") or [{}])[0]
    if task.get("status_code") != 20000:
        st.error(f"DataForSEO: {task.get('status_message')}")
        return None
    st.session_state.costs[path + key] = float(task.get("cost") or data.get("cost") or 0)
    return task.get("result") or []


def kw_row(it):
    ki = it.get("keyword_info") or {}
    return {
        "Keyword": it.get("keyword"),
        "Volume": ki.get("search_volume"),
        "KD": dig(it, "keyword_properties", "keyword_difficulty"),
        "CPC ($)": ki.get("cpc"),
        "Competition": ki.get("competition_level"),
        "Intent": dig(it, "search_intent_info", "main_intent"),
    }


def trend_df(item):
    rows = dig(item, "keyword_info", "monthly_searches", default=[]) or []
    df = pd.DataFrame(
        [{"Month": f"{r['year']}-{r['month']:02d}", "Searches": r.get("search_volume") or 0} for r in rows]
    )
    return df.sort_values("Month").set_index("Month") if not df.empty else df


def download(df, name):
    st.download_button("Download CSV", df.to_csv(index=False).encode("utf-8"), f"{name}.csv", "text/csv")


def ai_summary(context, df):
    """Optional: ask Claude for short, plain-language takeaways."""
    key = secret("ANTHROPIC_API_KEY")
    if not key:
        st.info("Add ANTHROPIC_API_KEY to your secrets to turn on AI summaries.")
        return
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    prompt = (
        "You are a B2B SEO strategist at Perceptric, an agency focused on bottom-of-funnel SEO "
        "and AI search. Review this data and reply with: 5 short takeaways, the best buying-intent "
        "opportunities, and any quick wins. Use plain, simple language and short bullets.\n\n"
        f"Context: {context}\n\nData (CSV, top rows):\n{df.head(60).to_csv(index=False)}"
    )
    with st.spinner("Claude is reading the data..."):
        msg = client.messages.create(
            model=secret("CLAUDE_MODEL", "claude-sonnet-5-5"),
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
    st.markdown(msg.content[0].text)


# ───────────────────────── look and feel ─────────────────────────
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
html, body, [class*="css"], .stMarkdown, .stTextInput, .stButton, .stDataFrame { font-family: 'Inter', sans-serif; }
h1, h2, h3 { font-weight: 600 !important; letter-spacing: -0.02em; }
[data-testid="stMetric"] { background: #F4F8FE; border: 2px solid #000; border-radius: 6px;
  padding: 14px 16px; box-shadow: 4px 4px 0 #0943B0; }
.stButton > button, .stDownloadButton > button { border: 2px solid #000; border-radius: 4px; font-weight: 500; }
.stButton > button[kind="primary"] { background: #0943B0; color: #fff; }
</style>
""",
    unsafe_allow_html=True,
)


# ───────────────────────── team password ─────────────────────────
def gate():
    pw = secret("APP_PASSWORD")
    if not pw or st.session_state.get("authed"):
        return True
    st.title("Perceptric SEO Research")
    entered = st.text_input("Team password", type="password")
    if entered:
        if entered == pw:
            st.session_state.authed = True
            st.rerun()
        st.error("Wrong password.")
    return False


if not gate():
    st.stop()

if not (secret("DFS_LOGIN") and secret("DFS_PASSWORD")):
    st.error("Add DFS_LOGIN and DFS_PASSWORD to your secrets first. See README.md.")
    st.stop()

st.session_state.setdefault("costs", {})

# ───────────────────────── sidebar ─────────────────────────
with st.sidebar:
    st.header("Settings")
    loc_name = st.selectbox("Location", list(LOCATIONS), index=0)
    lang_name = st.selectbox("Language", list(LANGUAGES), index=0)
    st.divider()
    st.metric("Spent this session", f"${sum(st.session_state.costs.values()):.3f}")
    st.caption("Repeat searches within 24 hours are free (cached).")

LOC, LANG = LOCATIONS[loc_name], LANGUAGES[lang_name]
BASE = {"location_code": LOC, "language_code": LANG}

st.title("Perceptric SEO Research")
tab_kw, tab_ideas, tab_domain, tab_serp = st.tabs(
    ["Keyword overview", "Keyword ideas", "Domain overview", "SERP check"]
)

# ───────────────────────── 1. Keyword overview ─────────────────────────
with tab_kw:
    raw = st.text_area("Keywords (one per line, up to 100)", placeholder="saas seo agency\nb2b seo agency")
    if st.button("Get keyword data", type="primary", key="kw_go"):
        kws = [k.strip().lower() for k in raw.splitlines() if k.strip()][:100]
        if not kws:
            st.warning("Add at least one keyword.")
        else:
            res = call("dataforseo_labs/google/keyword_overview/live", {**BASE, "keywords": kws})
            st.session_state.kw_items = (res[0].get("items") or []) if res else []

    items = st.session_state.get("kw_items")
    if items:
        df = pd.DataFrame([kw_row(i) for i in items]).sort_values("Volume", ascending=False, na_position="last")
        st.dataframe(df, width="stretch", hide_index=True)
        download(df, "keyword-overview")
        pick = st.selectbox("See the 12-month trend for", df["Keyword"].tolist())
        item = next(i for i in items if i.get("keyword") == pick)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Monthly searches", f"{dig(item, 'keyword_info', 'search_volume', default=0):,}")
        c2.metric("CPC", f"${dig(item, 'keyword_info', 'cpc', default=0) or 0:.2f}")
        c3.metric("Keyword difficulty", dig(item, "keyword_properties", "keyword_difficulty", default="–"))
        c4.metric("Intent", dig(item, "search_intent_info", "main_intent", default="–"))
        t = trend_df(item)
        if not t.empty:
            st.bar_chart(t, color="#0943B0")
    elif items == []:
        st.info("No data found for these keywords in this location.")

# ───────────────────────── 2. Keyword ideas ─────────────────────────
with tab_ideas:
    c1, c2 = st.columns([3, 1])
    seed = c1.text_input("Seed keyword", placeholder="seo agency")
    limit = c2.selectbox("How many ideas", [100, 300, 500, 1000], index=0)
    if st.button("Find keyword ideas", type="primary", key="ideas_go"):
        if not seed.strip():
            st.warning("Add a seed keyword.")
        else:
            res = call(
                "dataforseo_labs/google/keyword_ideas/live",
                {**BASE, "keywords": [seed.strip().lower()], "limit": limit,
                 "order_by": ["keyword_info.search_volume,desc"]},
            )
            st.session_state.ideas = (res[0].get("items") or []) if res else []
            st.session_state.ideas_seed = seed.strip()

    ideas = st.session_state.get("ideas")
    if ideas:
        df = pd.DataFrame([kw_row(i) for i in ideas])
        f1, f2, f3, f4 = st.columns(4)
        min_vol = f1.number_input("Min volume", 0, value=0, step=10)
        max_kd = f2.slider("Max KD", 0, 100, 100)
        intents = f3.multiselect("Intent", sorted(df["Intent"].dropna().unique()))
        must = f4.text_input("Must contain", placeholder="e.g. best, vs, agency")
        view = df[(df["Volume"].fillna(0) >= min_vol) & (df["KD"].fillna(0) <= max_kd)]
        if intents:
            view = view[view["Intent"].isin(intents)]
        if must.strip():
            words = [w.strip().lower() for w in must.split(",") if w.strip()]
            view = view[view["Keyword"].str.lower().apply(lambda k: any(w in k for w in words))]
        st.caption(f"{len(view)} of {len(df)} keywords")
        st.dataframe(view, width="stretch", hide_index=True)
        download(view, f"keyword-ideas-{st.session_state.get('ideas_seed', 'seed')}")
        if st.button("Summarize with Claude", key="ideas_ai"):
            ai_summary(f"Keyword ideas for '{st.session_state.get('ideas_seed')}' in {loc_name}", view)
    elif ideas == []:
        st.info("No ideas found. Try a broader seed keyword.")

# ───────────────────────── 3. Domain overview ─────────────────────────
with tab_domain:
    domain = st.text_input("Domain", placeholder="competitor.com")
    if st.button("Analyze domain", type="primary", key="dom_go"):
        d = domain.strip().lower().replace("https://", "").replace("http://", "").replace("www.", "").strip("/")
        if not d:
            st.warning("Add a domain.")
        else:
            st.session_state.dom = {
                "name": d,
                "rank": call("dataforseo_labs/google/domain_rank_overview/live", {**BASE, "target": d}),
                "kws": call("dataforseo_labs/google/ranked_keywords/live",
                            {**BASE, "target": d, "limit": 200,
                             "order_by": ["keyword_data.keyword_info.search_volume,desc"]}),
                "comp": call("dataforseo_labs/google/competitors_domain/live", {**BASE, "target": d, "limit": 10}),
                "links": call("backlinks/summary/live", {"target": d, "include_subdomains": True}),
            }

    dom = st.session_state.get("dom")
    if dom:
        organic = dig((dig((dom["rank"] or [{}])[0], "items", default=[{}]) or [{}])[0], "metrics", "organic", default={})
        links = (dom["links"] or [{}])[0] or {}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Organic keywords", f"{organic.get('count') or 0:,}")
        c2.metric("Est. monthly traffic", f"{round(organic.get('etv') or 0):,}")
        c3.metric("Top-3 rankings", f"{(organic.get('pos_1') or 0) + (organic.get('pos_2_3') or 0):,}")
        c4.metric("Referring domains", f"{links.get('referring_domains') or 0:,}")

        st.subheader("Top ranking keywords")
        kw_items = dig((dom["kws"] or [{}])[0], "items", default=[]) or []
        kdf = pd.DataFrame([{
            "Keyword": dig(i, "keyword_data", "keyword"),
            "Volume": dig(i, "keyword_data", "keyword_info", "search_volume"),
            "Position": dig(i, "ranked_serp_element", "serp_item", "rank_absolute"),
            "Est. traffic": round(dig(i, "ranked_serp_element", "serp_item", "etv", default=0) or 0),
            "URL": dig(i, "ranked_serp_element", "serp_item", "url"),
        } for i in kw_items])
        if not kdf.empty:
            st.dataframe(kdf, width="stretch", hide_index=True)
            download(kdf, f"ranked-keywords-{dom['name']}")
        else:
            st.info("No ranking keywords found in this location.")

        st.subheader("Organic competitors")
        comp_items = dig((dom["comp"] or [{}])[0], "items", default=[]) or []
        cdf = pd.DataFrame([{
            "Domain": i.get("domain"),
            "Shared keywords": i.get("intersections"),
            "Avg. position": round(i.get("avg_position") or 0, 1),
            "Their organic keywords": dig(i, "full_domain_metrics", "organic", "count"),
        } for i in comp_items if i.get("domain") != dom["name"]])
        if not cdf.empty:
            st.dataframe(cdf, width="stretch", hide_index=True)
            download(cdf, f"competitors-{dom['name']}")

        if not kdf.empty and st.button("Summarize with Claude", key="dom_ai"):
            ai_summary(f"Top ranking keywords for {dom['name']} in {loc_name}", kdf)

# ───────────────────────── 4. SERP check ─────────────────────────
with tab_serp:
    q = st.text_input("Keyword to check", placeholder="best b2b seo agency")
    if st.button("Check Google results", type="primary", key="serp_go"):
        if not q.strip():
            st.warning("Add a keyword.")
        else:
            st.session_state.serp = call(
                "serp/google/organic/live/advanced", {**BASE, "keyword": q.strip(), "depth": 10}
            )
    serp = st.session_state.get("serp")
    if serp:
        items = (serp[0] or {}).get("items") or []
        has_aio = any(i.get("type") == "ai_overview" for i in items)
        st.metric("AI Overview on this search", "Yes" if has_aio else "No")
        sdf = pd.DataFrame([{
            "Rank": i.get("rank_group"), "Domain": i.get("domain"),
            "Title": i.get("title"), "URL": i.get("url"),
        } for i in items if i.get("type") == "organic"])
        st.dataframe(sdf, width="stretch", hide_index=True)
        download(sdf, "serp")
