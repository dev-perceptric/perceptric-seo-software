"""
Perceptric SEO Research v2. An in-house Semrush-style research tool.
Data: DataForSEO API (pay per request). AI visibility: ChatGPT, Claude, Gemini and Perplexity APIs.
Run locally:  streamlit run app.py
"""
import html
import json
import os
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

import altair as alt
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="Perceptric SEO Research", page_icon="🔎", layout="wide")

API = "https://api.dataforseo.com/v3"
LOGO = "https://perceptric.com/wp-content/uploads/2026/03/Perceptric-_-Logo.png"

# Brand tokens (Perceptric brand system v3)
BLUE, BLUE_BG = "#0943B0", "#E8EFFC"
INK_2, G500, BORDER, LINE = "#2B3242", "#7C8697", "#D9DEE6", "#000000"

# Country: (DataForSEO location code, default language)
LOCATIONS = {
    "United States": (2840, "English"), "United Kingdom": (2826, "English"),
    "Canada": (2124, "English"), "Australia": (2036, "English"),
    "Singapore": (2702, "English"), "India": (2356, "English"),
    "Vietnam": (2704, "Vietnamese"), "Germany": (2276, "German"),
    "France": (2250, "French"), "Netherlands": (2528, "Dutch"),
    "Spain": (2724, "Spanish"), "Japan": (2392, "Japanese"),
}
LANGUAGES = {"English": "en", "Vietnamese": "vi", "German": "de", "French": "fr",
             "Dutch": "nl", "Spanish": "es", "Japanese": "ja"}
CODE_TO_COUNTRY = {code: name for name, (code, _) in LOCATIONS.items()}

INTENTS = ["informational", "navigational", "commercial", "transactional"]
BUCKETS = {"Top 3": (1, 3), "4-10": (4, 10), "11-20": (11, 20), "21-100": (21, 100)}
QUESTION_WORDS = {"how", "what", "why", "when", "where", "who", "which", "can", "does",
                  "do", "is", "are", "should", "will"}
STOPWORDS = {"the", "a", "an", "for", "of", "to", "in", "and", "or", "on", "with", "my", "your"}
FEATURES = {
    "ai_overview": "AI Overview", "featured_snippet": "Featured snippet",
    "people_also_ask": "People also ask", "local_pack": "Local pack", "map": "Map",
    "video": "Video", "short_videos": "Short videos", "images": "Images",
    "top_stories": "Top stories", "knowledge_graph": "Knowledge panel", "paid": "Ads",
    "shopping": "Shopping", "popular_products": "Popular products",
    "related_searches": "Related searches", "people_also_search": "People also search",
    "perspectives": "Perspectives", "discussions_and_forums": "Discussions and forums",
    "twitter": "X posts", "carousel": "Carousel", "answer_box": "Answer box", "jobs": "Jobs",
}

# Ideas: match type -> (endpoint, plain help text)
METHODS = {
    "Broad match": ("dataforseo_labs/google/keyword_ideas/live",
                    "Keywords from the same topic, even when they use other words."),
    "Phrase match": ("dataforseo_labs/google/keyword_suggestions/live",
                     "Keywords that contain your seed phrase."),
    "Related": ("dataforseo_labs/google/related_keywords/live",
                "Keywords pulled from Google's related searches, two levels deep."),
}


# ───────────────────────── data helpers ─────────────────────────
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


def first(res):
    """First result object of a DataForSEO task, or an empty dict."""
    return ((res or [{}])[0]) or {}


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


def call(path, payload, quiet=False):
    """Call an endpoint and return the task's result list (or None on error)."""
    key = json.dumps([payload], sort_keys=True)

    def fail(msg):
        if not quiet:
            st.error(msg)
        return None

    try:
        data = _post(path, key)
    except requests.HTTPError as e:
        return fail(f"DataForSEO returned an error: {e}")
    except Exception as e:
        return fail(f"Could not reach DataForSEO: {e}")
    if data.get("status_code") != 20000:
        return fail(f"DataForSEO: {data.get('status_message')}")
    task = (data.get("tasks") or [{}])[0]
    st.session_state.costs[path + key] = float(task.get("cost") or data.get("cost") or 0)
    if task.get("status_code") != 20000:
        return fail(f"DataForSEO: {task.get('status_message')}")
    return task.get("result") or []


def clean_domain(s):
    s = s.strip().lower()
    for p in ("https://", "http://", "www."):
        s = s.removeprefix(p)
    return s.split("/")[0]


def fmt(n):
    """Short numbers like Semrush: 3.9K, 1.2M."""
    if n is None or (isinstance(n, float) and pd.isna(n)):
        return "–"
    n = float(n)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suf
    return f"{n:,.0f}"


def kd_label(kd):
    if kd is None or pd.isna(kd):
        return None
    for cap, name in ((14, "Very easy"), (29, "Easy"), (49, "Possible"), (69, "Difficult"), (84, "Hard")):
        if kd <= cap:
            return name
    return "Very hard"


def feature_names(types):
    return [FEATURES.get(t, t.replace("_", " ").capitalize()) for t in (types or []) if t != "organic"]


def kw_row(it):
    it = it.get("keyword_data", it)  # related_keywords nests the data one level down
    ki = it.get("keyword_info") or {}
    kd = dig(it, "keyword_properties", "keyword_difficulty")
    return {
        "Keyword": it.get("keyword"),
        "Volume": ki.get("search_volume"),
        "KD": kd,
        "KD level": kd_label(kd),
        "CPC": ki.get("cpc"),
        "Competition": ki.get("competition_level"),
        "Intent": dig(it, "search_intent_info", "main_intent"),
        "SERP features": ", ".join(feature_names(dig(it, "serp_info", "serp_item_types", default=[]))),
    }


def trend_df(item):
    item = item.get("keyword_data", item)
    rows = dig(item, "keyword_info", "monthly_searches", default=[]) or []
    df = pd.DataFrame([{"date": pd.Timestamp(r["year"], r["month"], 1), "Searches": r.get("search_volume") or 0}
                       for r in rows])
    if df.empty:
        return df
    df = df.sort_values("date")
    df["Month"] = df["date"].dt.strftime("%b %y")
    return df[["Month", "Searches"]]


def hist_df(res):
    rows = []
    for it in first(res).get("items") or []:
        o = dig(it, "metrics", "organic", default={}) or {}
        rows.append({"Month": f"{it['year']}-{it['month']:02d}-01",
                     "Traffic": round(o.get("etv") or 0), "Keywords": o.get("count") or 0})
    df = pd.DataFrame(rows)
    return df.sort_values("Month").reset_index(drop=True) if not df.empty else df


def delta(hist, col):
    """Month over month change, as a pill note and tone."""
    if len(hist) < 2:
        return None, None
    prev, last = hist[col].iloc[-2], hist[col].iloc[-1]
    if not prev:
        return None, None
    d = (last - prev) / prev * 100
    return f"{d:+.1f}%", ("up" if d >= 0 else "down")


def position_buckets(organic):
    pos = lambda k: organic.get(k) or 0
    rest = sum(pos(f"pos_{a}_{a + 9}") for a in range(21, 100, 10))
    return pd.DataFrame({"Positions": list(BUCKETS),
                         "Keywords": [pos("pos_1") + pos("pos_2_3"), pos("pos_4_10"), pos("pos_11_20"), rest]})


def ranked_df(res):
    rows = []
    for i in first(res).get("items") or []:
        kd = dig(i, "keyword_data", "keyword_properties", "keyword_difficulty")
        rows.append({
            "Keyword": dig(i, "keyword_data", "keyword"),
            "Position": dig(i, "ranked_serp_element", "serp_item", "rank_group"),
            "Volume": dig(i, "keyword_data", "keyword_info", "search_volume"),
            "KD": kd,
            "Intent": dig(i, "keyword_data", "search_intent_info", "main_intent"),
            "CPC": dig(i, "keyword_data", "keyword_info", "cpc"),
            "Est. traffic": round(dig(i, "ranked_serp_element", "serp_item", "etv", default=0) or 0),
            "URL": dig(i, "ranked_serp_element", "serp_item", "url"),
            "SERP features": ", ".join(feature_names(dig(i, "keyword_data", "serp_info", "serp_item_types", default=[]))),
        })
    return pd.DataFrame(rows)


def intent_df(kdf):
    if kdf.empty or "Intent" not in kdf:
        return pd.DataFrame()
    counts = kdf["Intent"].dropna().value_counts()
    total = counts.sum()
    if not total:
        return pd.DataFrame()
    return pd.DataFrame([{"Intent": f"{i.capitalize()} ({counts.get(i, 0) / total:.0%})",
                          "Keywords": int(counts.get(i, 0))} for i in INTENTS])


def pages_df(res, total):
    rows = []
    for i in first(res).get("items") or []:
        o = dig(i, "metrics", "organic", default={}) or {}
        p = lambda k: o.get(k) or 0
        etv = o.get("etv") or 0
        rows.append({
            "Page": i.get("page_address"),
            "Est. traffic": round(etv),
            "Traffic share": (etv / total * 100) if total else None,
            "Keywords": o.get("count"),
            "Top 3": p("pos_1") + p("pos_2_3"),
            "Top 10": p("pos_1") + p("pos_2_3") + p("pos_4_10"),
        })
    return pd.DataFrame(rows)


def competitors_df(res, me):
    rows = []
    for i in first(res).get("items") or []:
        if i.get("domain") == me:
            continue
        rows.append({
            "Domain": i.get("domain"),
            "Shared keywords": i.get("intersections"),
            "Avg. position": round(i.get("avg_position") or 0, 1),
            "Their keywords": dig(i, "full_domain_metrics", "organic", "count"),
            "Their traffic": round(dig(i, "full_domain_metrics", "organic", "etv", default=0) or 0),
        })
    return pd.DataFrame(rows)


def regions_for(domain):
    """Organic keywords and traffic per country. One call with no location returns every country.
    If that comes back thin, fall back to one call per country in LOCATIONS."""
    path = "dataforseo_labs/google/domain_rank_overview/live"
    items = first(call(path, {"target": domain}, quiet=True)).get("items") or []
    if len(items) <= 1:
        items = []
        for code, lang in LOCATIONS.values():
            r = call(path, {"target": domain, "location_code": code, "language_code": LANGUAGES[lang]}, quiet=True)
            items += [{**i, "location_code": i.get("location_code") or code} for i in (first(r).get("items") or []) if i]
    rows = []
    for it in items:
        o = dig(it, "metrics", "organic", default={}) or {}
        if not o.get("count"):
            continue
        code = it.get("location_code")
        rows.append({"Country": CODE_TO_COUNTRY.get(code, f"Location {code}"), "Code": code,
                     "Keywords": o.get("count"), "Est. traffic": round(o.get("etv") or 0)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("Est. traffic", ascending=False).reset_index(drop=True)
    total = df["Est. traffic"].sum()
    df["Traffic share"] = df["Est. traffic"] / total * 100 if total else 0
    return df


def word_groups(keywords, seed, n=25):
    """Most common words across the ideas, like Semrush Keyword Magic groups."""
    seed_words = set(seed.lower().split())
    c = Counter(w for k in keywords for w in set(str(k).lower().split())
                if w not in seed_words and w not in STOPWORDS and len(w) > 1)
    return c.most_common(n)


def contains_any(series, text):
    words = [w.strip().lower() for w in text.split(",") if w.strip()]
    return series.fillna("").str.lower().apply(lambda k: any(w in k for w in words))


def short_path(u):
    path = urlparse(u or "").path or "/"
    return path if len(path) <= 48 else path[:45] + "..."


def ideas_payload(method, seed, limit):
    if method == "Broad match":
        return {**BASE, "keywords": [seed], "limit": limit, "order_by": ["keyword_info.search_volume,desc"]}
    if method == "Phrase match":
        return {**BASE, "keyword": seed, "limit": limit, "order_by": ["keyword_info.search_volume,desc"]}
    return {**BASE, "keyword": seed, "depth": 2, "limit": limit,
            "order_by": ["keyword_data.keyword_info.search_volume,desc"]}


# ───────────────────────── UI helpers ─────────────────────────
COLS = {
    "Volume": st.column_config.NumberColumn(format="localized"),
    "KD": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
    "CPC": st.column_config.NumberColumn(format="dollar"),
    "Position": st.column_config.NumberColumn(format="%d"),
    "Est. traffic": st.column_config.NumberColumn(format="localized"),
    "Keywords": st.column_config.NumberColumn(format="localized"),
    "Shared keywords": st.column_config.NumberColumn(format="localized"),
    "Their keywords": st.column_config.NumberColumn(format="localized"),
    "Their traffic": st.column_config.NumberColumn(format="localized"),
    "Avg. position": st.column_config.NumberColumn(format="%.1f"),
    "Traffic share": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f%%"),
    "URL": st.column_config.LinkColumn(),
    "Page": st.column_config.LinkColumn(),
}


def table(df, styler=None, height=None):
    cfg = {c: COLS[c] for c in df.columns if c in COLS}
    kwargs = {"height": height} if height else {}
    st.dataframe(styler if styler is not None else df, width="stretch", hide_index=True,
                 column_config=cfg, **kwargs)


def download(df, name):
    st.download_button("Download CSV", df.to_csv(index=False).encode("utf-8"), f"{name}.csv",
                       "text/csv", key=f"dl_{name}")


_frames = [0]


def frame(title=None):
    """A brand frame: white card, 2px black border, hard blue shadow."""
    _frames[0] += 1
    box = st.container(key=f"pcf_{_frames[0]}")
    if title:
        box.markdown(f'<div class="pc-head">{html.escape(title)}</div>', unsafe_allow_html=True)
    return box


def cards(items, cols=4):
    """items: (label, value, note, tone). tone is 'up', 'down' or 'info'."""
    out = []
    for label, value, note, tone in items:
        pill = f'<span class="pc-pill pc-pill--{tone or "info"}">{html.escape(str(note))}</span>' if note else ""
        out.append(f'<div class="pc-card"><div class="pc-card__lab">{html.escape(label)}</div>'
                   f'<div class="pc-card__num">{html.escape(str(value))}{pill}</div></div>')
    st.markdown(f'<div class="pc-cards" style="--cols:{cols}">{"".join(out)}</div>', unsafe_allow_html=True)


def chips(labels, on=()):
    if not labels:
        st.caption("No special features on this search.")
        return
    out = "".join(f'<span class="pc-chipx{" is-on" if l in on else ""}">{html.escape(l)}</span>' for l in labels)
    st.markdown(f'<div class="pc-chips">{out}</div>', unsafe_allow_html=True)


def bullets(lines):
    out = "".join(f"<li>{html.escape(str(l))}</li>" for l in lines)
    st.markdown(f'<ul class="pc-ul">{out}</ul>', unsafe_allow_html=True)


def _style(chart, height):
    return (chart.properties(height=height)
            .configure(font="Inter", background="transparent")
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor=G500, titleColor=G500, labelFontSize=12, titleFontSize=12,
                            gridColor=BORDER, gridDash=[3, 5], domainColor=LINE, domainWidth=2,
                            tickColor=LINE, labelLimit=260))


def bars(df, cat, val, horizontal=False, height=260):
    """Brand bars: pale blue with black outline. The biggest bar is solid blue."""
    d = df[[cat, val]].rename(columns={cat: "c", val: "v"}).copy()
    d["hl"] = d["v"] == d["v"].max()
    color = alt.Color("hl:N", scale=alt.Scale(domain=[False, True], range=[BLUE_BG, BLUE]), legend=None)
    c_enc = (alt.Y("c:N", sort=None, title=None) if horizontal
             else alt.X("c:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)))
    v_enc = alt.X("v:Q", title=None) if horizontal else alt.Y("v:Q", title=None)
    chart = alt.Chart(d).mark_bar(stroke=LINE, strokeWidth=1.5, cornerRadius=2).encode(
        x=v_enc if horizontal else c_enc,
        y=c_enc if horizontal else v_enc,
        color=color,
        tooltip=[alt.Tooltip("c:N", title=cat), alt.Tooltip("v:Q", title=val, format=",.0f")],
    )
    st.altair_chart(_style(chart, height), width="stretch")


def trend(df, x, y, height=260):
    """Brand line: blue 3px line over a pale area, white dots, solid end dot."""
    d = df[[x, y]].rename(columns={x: "x", y: "y"}).copy()
    d["x"] = pd.to_datetime(d["x"])
    base = alt.Chart(d).encode(
        x=alt.X("x:T", title=None, axis=alt.Axis(format="%b %y", labelAngle=0, grid=False)),
        y=alt.Y("y:Q", title=None),
    )
    tip = [alt.Tooltip("x:T", title="Month", format="%B %Y"), alt.Tooltip("y:Q", title=y, format=",.0f")]
    area = base.mark_area(color=BLUE_BG, opacity=0.9)
    line = base.mark_line(color=BLUE, strokeWidth=3, strokeCap="round", strokeJoin="round")
    dots = base.mark_point(filled=True, fill="#FFFFFF", stroke=BLUE, strokeWidth=2.5, size=45).encode(tooltip=tip)
    end = alt.Chart(d.tail(1)).mark_point(filled=True, fill=BLUE, stroke=LINE, strokeWidth=2, size=90).encode(
        x="x:T", y="y:Q", tooltip=tip)
    st.altair_chart(_style(area + line + dots + end, height), width="stretch")


def header(title="SEO Research"):
    st.markdown(
        f'<div class="pc-top"><img src="{LOGO}" alt="Perceptric" '
        f'style="height:28px;width:auto;max-width:150px"><span class="pc-top__t">{title}</span></div>',
        unsafe_allow_html=True,
    )


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
    with frame("Claude summary"):
        st.markdown(msg.content[0].text)


# ───────────────────────── AI visibility helpers ─────────────────────────
AI_ENGINES = {  # engine: (API key name, model setting name, default model)
    "ChatGPT": ("OPENAI_API_KEY", "OPENAI_MODEL", "gpt-5.2"),
    "Claude": ("ANTHROPIC_API_KEY", "CLAUDE_MODEL", "claude-sonnet-5-5"),
    "Gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", "gemini-3-flash-preview"),
    "Perplexity": ("PERPLEXITY_API_KEY", "PERPLEXITY_MODEL", "sonar"),
}
AI_WEB = str(secret("USE_WEB_SEARCH", "true")).lower() == "true"
AI_WORKERS = int(secret("MAX_WORKERS", 8))

KEYWORD_PROMPT = """You are a B2B SEO strategist. Analyze this company website and return JSON only, with no other text.

Website: {url}
Page title: {title}
Meta description: {desc}
Page text: {text}

Return exactly this JSON shape:
{{"brand": "brand name as customers say it", "aliases": ["other names, product names, or spellings"], "category": "one-line description of what the company sells", "keywords": ["20 keywords"]}}

Keyword rules:
- Exactly 20 bottom-of-funnel keywords a buyer types when ready to choose a vendor.
- Every keyword starts with "best" and follows the formula "best [X] software", "best [X] services", or "best [X] tools".
- MECE: each keyword covers a distinct use case, audience, industry, or segment. No overlap and no near duplicates. Together they cover every category this company competes in.
- Use the buyer's language. Never include the brand name.
- All lowercase."""

QUERY_PROMPT = """Recommend me 10 {keyword}.

Answer in markdown. Use a numbered list from 1 to 10. Start each item with the name in bold, followed by one sentence on why you recommend it."""


def ask_claude(prompt, key, model, web=True, max_tokens=2500):
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    kwargs = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if web:
        kwargs["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    msg = client.messages.create(**kwargs)
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def ask_openai(prompt, key, model, web=True):
    from openai import OpenAI

    client = OpenAI(api_key=key)
    kwargs = {"model": model, "input": prompt}
    if web:
        kwargs["tools"] = [{"type": "web_search"}]
    return client.responses.create(**kwargs).output_text


def ask_gemini(prompt, key, model, web=True):
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]) if web else None
    return client.models.generate_content(model=model, contents=prompt, config=config).text or ""


def ask_perplexity(prompt, key, model, web=True):
    from openai import OpenAI

    client = OpenAI(api_key=key, base_url="https://api.perplexity.ai")
    resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
    return resp.choices[0].message.content


ASK = {"ChatGPT": ask_openai, "Claude": ask_claude, "Gemini": ask_gemini, "Perplexity": ask_perplexity}


def run_one(engine, keyword, key, model, web):
    """Runs in a worker thread, so it gets its key and model passed in."""
    prompt = QUERY_PROMPT.format(keyword=keyword)
    last_error = ""
    for _ in range(2):
        try:
            text = ASK[engine](prompt, key, model, web) or ""
            return {"engine": engine, "keyword": keyword, "response": re.sub(r"\[\d+\]", "", text), "error": ""}
        except Exception as e:
            last_error = str(e)[:300]
            time.sleep(2)
    return {"engine": engine, "keyword": keyword, "response": "", "error": last_error}


def normalize_url(url):
    url = url.strip()
    return url if url.startswith("http") else "https://" + url


def fetch_site(url):
    try:
        from bs4 import BeautifulSoup

        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (compatible; PerceptricBot/1.0)"})
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        title = soup.title.get_text(strip=True) if soup.title else ""
        meta = soup.find("meta", attrs={"name": "description"})
        desc = meta.get("content", "") if meta else ""
        text = " ".join(soup.get_text(" ").split())[:8000]
        return title, desc, text
    except Exception:
        return "", "", ""


def parse_json(text):
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start: end + 1])


def generate_profile(url, key, model):
    title, desc, text = fetch_site(url)
    raw = ask_claude(KEYWORD_PROMPT.format(url=url, title=title, desc=desc, text=text or "Not available"),
                     key, model, web=not text, max_tokens=2000)
    data = parse_json(raw)
    data["keywords"] = [k.strip().lower() for k in data.get("keywords", []) if k.strip()][:20]
    data["url"] = url
    return data


ITEM_RE = re.compile(r"^\s*(?:#+\s*)?(\d{1,2})[.)]\s+(.*)$")


def parse_items(md):
    items = []
    for line in md.splitlines():
        m = ITEM_RE.match(line)
        if not m:
            continue
        rest = m.group(2)
        bold = re.search(r"\*\*(.+?)\*\*", rest)
        name = bold.group(1) if bold else re.split(r"\s[-–:]\s|:", rest)[0]
        name = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", name)
        name = re.sub(r"[\[\]\*`#]", "", name).strip(" :-–")
        if name:
            items.append((int(m.group(1)), name[:80]))
    return items[:15]


def brand_terms(brand, aliases, url):
    host = urlparse(url).netloc.lower().removeprefix("www.")
    root = host.split(".")[0]
    terms = {brand.lower().strip(), host, root} | {a.lower().strip() for a in aliases}
    return sorted(t for t in terms if len(t) >= 3)


def evaluate(text, terms):
    items = parse_items(text)
    rank = None
    for pos, name in items:
        if any(t in name.lower() for t in terms):
            rank = pos
            break
    low = text.lower()
    mentioned = rank is not None or any(
        re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", low) for t in terms)
    if rank:
        score = max(10, 110 - 10 * rank)
    elif mentioned:
        score = 25
    else:
        score = 0
    return items, mentioned, rank, score


def norm_name(name):
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def build_ai_frames(results, terms):
    rows = []
    for r in results:
        items, mentioned, rank, score = evaluate(r["response"], terms) if not r["error"] else ([], False, None, 0)
        rows.append({
            "Engine": r["engine"], "Keyword": r["keyword"], "Mentioned": "Yes" if mentioned else "No",
            "Rank": rank, "Score": score, "Top 3": ", ".join(n for _, n in items[:3]),
            "Items": items, "Response": r["response"], "Error": r["error"],
        })
    df = pd.DataFrame(rows)

    comp = defaultdict(lambda: {"name": "", "count": 0, "engines": set(), "ranks": []})
    for _, row in df.iterrows():
        for pos, name in row["Items"]:
            k = norm_name(name)
            if not k or any(t in name.lower() for t in terms):
                continue
            c = comp[k]
            c["name"] = c["name"] or name
            c["count"] += 1
            c["engines"].add(row["Engine"])
            c["ranks"].append(pos)
    comp_df = pd.DataFrame([
        {"Brand": v["name"], "Mentions": v["count"], "Engines": len(v["engines"]),
         "Avg rank": round(sum(v["ranks"]) / len(v["ranks"]), 1)}
        for v in comp.values()
    ])
    if not comp_df.empty:
        comp_df = comp_df.sort_values(["Mentions", "Avg rank"], ascending=[False, True]).head(25).reset_index(drop=True)
    return df, comp_df


def engine_summary(df):
    out = []
    for engine, g in df.groupby("Engine"):
        ranked = g["Rank"].dropna()
        out.append({
            "Engine": engine,
            "Visibility score": round(g["Score"].mean(), 1),
            "Mention rate": round((g["Mentioned"] == "Yes").mean() * 100),
            "Avg rank": round(ranked.mean(), 1) if len(ranked) else None,
            "Prompts": len(g),
        })
    return pd.DataFrame(out).sort_values("Visibility score", ascending=False).reset_index(drop=True)


def build_ai_markdown(profile, df, summary, comp_df):
    lines = [
        f"# AI Visibility Report: {profile['brand']}", "",
        f"Website: {profile['url']}",
        f"Date: {datetime.now():%Y-%m-%d}",
        f"Visibility score: {df['Score'].mean():.1f} / 100",
        f"Mention rate: {(df['Mentioned'] == 'Yes').mean() * 100:.0f}%", "",
        "## Score by engine", "",
        "| Engine | Visibility score | Mention rate | Avg rank |", "|---|---|---|---|",
    ]
    for _, s in summary.iterrows():
        avg = s["Avg rank"] if pd.notna(s["Avg rank"]) else "-"
        lines.append(f"| {s['Engine']} | {s['Visibility score']} | {s['Mention rate']}% | {avg} |")
    if not comp_df.empty:
        lines += ["", "## Top competitors", "", "| Brand | Mentions | Engines | Avg rank |", "|---|---|---|---|"]
        for _, c in comp_df.iterrows():
            lines.append(f"| {c['Brand'].replace('|', '/')} | {c['Mentions']} | {c['Engines']} | {c['Avg rank']} |")
    for engine, g in df.groupby("Engine"):
        lines += ["", f"## {engine}", "", "| Keyword | Mentioned | Rank | Score |", "|---|---|---|---|"]
        for _, r in g.iterrows():
            rank = int(r["Rank"]) if pd.notna(r["Rank"]) else "-"
            lines.append(f"| {r['Keyword']} | {r['Mentioned']} | {rank} | {r['Score']} |")
        for _, r in g.iterrows():
            lines += ["", f"### {r['Keyword']}", "", r["Response"] or f"Error: {r['Error']}"]
    return "\n".join(lines)


AI_COLS = {
    "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
    "Visibility score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
    "Mention rate": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%"),
    "Rank": st.column_config.NumberColumn(format="%d"),
    "Avg rank": st.column_config.NumberColumn(format="%.1f"),
    "Mentions": st.column_config.NumberColumn(format="localized"),
}


def ai_table(df, cfg=None, height=None):
    cfg = cfg or {c: AI_COLS[c] for c in df.columns if c in AI_COLS}
    kwargs = {"height": height} if height else {}
    st.dataframe(df, width="stretch", hide_index=True, column_config=cfg, **kwargs)


def render_ai_report(profile, df, comp_df):
    summary = engine_summary(df)
    ranked = df["Rank"].dropna()
    score = df["Score"].mean()

    cards([
        ("AI visibility", f"{score:.0f}", "out of 100", "info"),
        ("Mention rate", f"{(df['Mentioned'] == 'Yes').mean() * 100:.0f}%", None, None),
        ("Avg rank when listed", f"{ranked.mean():.1f}" if len(ranked) else "–", None, None),
        ("Prompts run", str(len(df)), None, None),
    ])
    errors = df[df["Error"] != ""]
    if len(errors):
        st.warning(f"{len(errors)} prompts failed. Open the engine tabs to see why.")

    left, right = st.columns([2, 3], gap="medium")
    with left:
        with frame("Score by engine"):
            bars(summary, "Engine", "Visibility score", height=240)
    with right:
        with frame("Engine breakdown"):
            ai_table(summary)

    engines = list(summary["Engine"])
    sub = st.tabs(engines + ["Competitors", "Keywords"])
    for i, engine in enumerate(engines):
        with sub[i]:
            g = df[df["Engine"] == engine]
            with frame(f"{engine} answers"):
                ai_table(g[["Keyword", "Mentioned", "Rank", "Score", "Top 3"]])
            for _, r in g.iterrows():
                tag = f"#{int(r['Rank'])}" if pd.notna(r["Rank"]) else ("mentioned" if r["Mentioned"] == "Yes" else "not mentioned")
                with st.expander(f"{r['Keyword']}  ({tag})"):
                    if r["Error"]:
                        st.error(r["Error"])
                    else:
                        st.markdown(r["Response"])

    with sub[-2]:
        if comp_df.empty:
            st.info("No competitors found.")
        else:
            with frame("Brands AI recommends most"):
                bars(comp_df.head(10), "Brand", "Mentions", horizontal=True, height=320)
            with frame("All competitors"):
                ai_table(comp_df)

    with sub[-1]:
        pivot = df.pivot_table(index="Keyword", columns="Engine", values="Score", aggfunc="mean")
        pivot["Average"] = pivot.mean(axis=1).round(1)
        pivot = pivot.sort_values("Average", ascending=False).reset_index()
        cfg = {c: st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f")
               for c in pivot.columns if c != "Keyword"}
        with frame("Score by keyword"):
            ai_table(pivot, cfg=cfg, height=560)

    slug = re.sub(r"[^a-z0-9]+", "-", profile["brand"].lower()).strip("-")
    d1, d2, _ = st.columns([1, 1, 2])
    d1.download_button("Download CSV", df.drop(columns=["Items"]).to_csv(index=False).encode(),
                       file_name=f"{slug}-ai-visibility.csv", mime="text/csv", key="ai_dl_csv", width="stretch")
    d2.download_button("Download Markdown report", build_ai_markdown(profile, df, summary, comp_df).encode(),
                       file_name=f"{slug}-ai-visibility.md", mime="text/markdown", key="ai_dl_md", width="stretch")


# ───────────────────────── look and feel (Perceptric neo-brutalist) ─────────────────────────
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

html, body, .stApp, .stMarkdown, .stCaption, button, input, textarea, [data-testid="stWidgetLabel"] {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}
.stApp { background: #F8F8FF; color: #2B3242; -webkit-font-smoothing: antialiased; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 2.4rem; padding-bottom: 3rem; max-width: 1280px; }
h1, h2, h3, h4 { font-weight: 500 !important; letter-spacing: -0.02em; color: #0B0D12; }
.stCaption, [data-testid="stCaptionContainer"] { color: #4A5363 !important; }

/* Top bar */
.pc-top { display: flex; align-items: center; gap: 14px; margin: 0 0 18px; padding: 0 0 16px;
  border-bottom: 2px solid #000; }
.pc-top img { height: 28px !important; width: auto !important; max-width: 150px !important; display: block; }
.pc-top__t { font-size: 26px; font-weight: 500; letter-spacing: -0.025em; color: #0B0D12; line-height: 1.1; }

/* Tabs */
.stTabs [data-baseweb="tab-list"] { gap: 6px; border-bottom: 2px solid #000; }
.stTabs [data-baseweb="tab"] { height: auto; padding: 10px 16px; background: transparent;
  border: 2px solid transparent; border-bottom: 0; border-radius: 4px 4px 0 0; margin-bottom: -2px; }
.stTabs [data-baseweb="tab"] p { font-size: 15px !important; font-weight: 500; color: #4A5363; }
.stTabs [data-baseweb="tab"]:hover p { color: #0943B0; }
.stTabs [aria-selected="true"] { background: #0943B0 !important; border-color: #000 !important; }
.stTabs [aria-selected="true"] p { color: #FFFFFF !important; }
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display: none; }
.stTabs [data-baseweb="tab-panel"] { padding-top: 22px; }

/* Buttons */
.stButton button, .stDownloadButton button {
  border: 2px solid #000 !important; border-radius: 4px !important; background: #FFFFFF; color: #0B0D12;
  box-shadow: none; transition: transform .15s cubic-bezier(.2,.7,.2,1), box-shadow .15s cubic-bezier(.2,.7,.2,1), background .15s;
}
.stButton button p, .stDownloadButton button p { font-weight: 500 !important; font-size: 15px; }
.stButton button:hover, .stDownloadButton button:hover {
  transform: translate(-2px,-2px); box-shadow: 4px 4px 0 #0943B0; color: #0B0D12; border-color: #000 !important; }
.stButton button[kind="primary"], [data-testid="stBaseButton-primary"] { background: #0943B0 !important; color: #FFFFFF !important; }
.stButton button[kind="primary"] p { color: #FFFFFF !important; }
.stButton button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {
  background: #062E7A !important; box-shadow: 4px 4px 0 #000; }
.stButton button:focus-visible, .stDownloadButton button:focus-visible { outline: 2px solid #0943B0; outline-offset: 3px; }

/* Inputs: 1px border, black bottom edge, blue focus ring */
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div, [data-testid="stNumberInputContainer"] {
  background: #FFFFFF !important; border: 1px solid #D9DEE6 !important; border-bottom-color: #000 !important;
  border-radius: 4px !important; }
[data-testid="stNumberInputContainer"] [data-baseweb="input"] { border: 0 !important; box-shadow: none !important; }
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within, [data-baseweb="select"] > div:focus-within,
[data-testid="stNumberInputContainer"]:focus-within {
  border-color: #0943B0 !important; box-shadow: 0 0 0 3px #E8EFFC !important; }
input::placeholder, textarea::placeholder { color: #A6AEBB !important; }
[data-testid="stWidgetLabel"] p { font-size: 14px !important; font-weight: 500 !important; color: #232A38 !important; }
[data-baseweb="tag"] { background: #E8EFFC !important; border-radius: 2px !important; }
[data-baseweb="tag"] span { color: #0943B0 !important; font-weight: 500; }

/* Sidebar */
[data-testid="stSidebar"] { background: #F4F8FE; border-right: 2px solid #000; }
[data-testid="stMetric"] { background: #FFFFFF; border: 2px solid #000; border-radius: 6px; padding: 14px 16px;
  box-shadow: 4px 4px 0 #0943B0; }
[data-testid="stMetricValue"] { color: #0943B0; font-weight: 500; letter-spacing: -0.02em; }

/* Alerts */
[data-testid="stAlert"], [data-testid="stAlertContainer"] { border: 2px solid #000; border-radius: 6px; }

/* Frames (st.container with key pcf_*) */
[class*="st-key-pcf_"] { background: #FFFFFF; border: 2px solid #000; border-radius: 6px;
  box-shadow: 6px 6px 0 #0943B0; padding: 20px 24px 24px; margin: 0 6px 8px 0; }
.pc-head { display: flex; align-items: center; gap: 12px; font-size: 18px; font-weight: 500;
  letter-spacing: -0.015em; line-height: 1.35; color: #0B0D12; margin: 0 0 4px; }
.pc-head::before { content: ''; width: 8px; height: 8px; background: #0943B0; flex-shrink: 0; }

/* Expanders (AI answers) */
[data-testid="stExpander"] details { background: #FFFFFF; border: 1px solid #000 !important; border-radius: 4px !important; }
[data-testid="stExpander"] summary p { font-size: 15px; font-weight: 500; color: #0B0D12; }
[data-testid="stExpander"] summary:hover p { color: #0943B0; }

/* Tables */
[data-testid="stDataFrame"] { border: 1px solid #000; border-radius: 4px; overflow: hidden; background: #FFFFFF; }

/* Stat cards */
.pc-cards { display: grid; grid-template-columns: repeat(var(--cols, 4), minmax(0, 1fr)); gap: 16px;
  margin: 0; padding: 0 6px 12px 0; }
.pc-card { background: #FFFFFF; border: 2px solid #000; border-radius: 6px; box-shadow: 4px 4px 0 #0943B0;
  padding: 16px 18px; min-width: 0; }
.pc-card__lab { font-size: 14px; font-weight: 500; color: #4A5363; margin: 0 0 6px; }
.pc-card__num { display: flex; align-items: baseline; flex-wrap: wrap; gap: 8px; font-size: 28px; font-weight: 500;
  letter-spacing: -0.03em; line-height: 1.15; color: #0943B0; font-variant-numeric: tabular-nums; }
.pc-pill { font-size: 12px; font-weight: 500; letter-spacing: 0; line-height: 1.3; padding: 3px 8px;
  border-radius: 3px; white-space: nowrap; }
.pc-pill--info { color: #0943B0; background: #E8EFFC; }
.pc-pill--up { color: #0B0D12; background: #D6EFDC; }
.pc-pill--down { color: #B3241A; background: #FFE1DE; }

/* SERP feature chips */
.pc-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 0; padding: 4px 0 4px; }
.pc-chipx { font-size: 14px; color: #232A38; background: #FFFFFF; border: 1px solid #D9DEE6;
  border-radius: 999px; padding: 6px 13px; }
.pc-chipx.is-on { background: #0B0D12; border-color: #0B0D12; color: #FFFFFF; }

/* Lists: blue squares */
.pc-ul { list-style: none; margin: 0; padding: 4px 0 4px; }
.pc-ul li { display: flex; gap: 12px; font-size: 16px; line-height: 1.6; color: #2B3242; margin: 0; padding: 0; }
.pc-ul li + li { margin-top: 10px; }
.pc-ul li::before { content: ''; width: 6px; height: 6px; background: #0943B0; flex-shrink: 0; margin-top: 10px; }
.pc-ul li::marker { content: none; }

@media (max-width: 720px) {
  .pc-cards { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
  .pc-card__num { font-size: 22px; }
  [class*="st-key-pcf_"] { padding: 16px 16px 20px; box-shadow: 5px 5px 0 #0943B0; }
  .pc-top__t { font-size: 22px; }
}
@media (prefers-reduced-motion: reduce) {
  .stButton button, .stDownloadButton button { transition: none !important; }
  .stButton button:hover, .stDownloadButton button:hover { transform: none; }
}
</style>
""",
    unsafe_allow_html=True,
)


# ───────────────────────── team password ─────────────────────────
def gate():
    pw = secret("APP_PASSWORD")
    if not pw or st.session_state.get("authed"):
        return True
    header()
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
    st.subheader("Settings")
    loc_name = st.selectbox("Location", list(LOCATIONS), index=0)
    loc_code, default_lang = LOCATIONS[loc_name]
    lang_name = st.selectbox("Language", list(LANGUAGES), index=list(LANGUAGES).index(default_lang),
                             key=f"lang_{loc_name}")
    st.divider()
    st.metric("Spent this session", f"${sum(st.session_state.costs.values()):.3f}")
    st.caption("Repeat searches within 24 hours are free (cached).")

LANG = LANGUAGES[lang_name]
BASE = {"location_code": loc_code, "language_code": LANG}

header()
tab_domain, tab_rank, tab_pages, tab_kw, tab_ideas, tab_serp, tab_ai = st.tabs(
    ["Domain overview", "Organic rankings", "Top pages", "Keyword overview", "Keyword ideas", "SERP check",
     "AI visibility"]
)


def domain_organic(dom):
    item = (first(dom["rank"]).get("items") or [{}])[0] or {}
    return (dig(item, "metrics", "organic", default={}) or {}), (dig(item, "metrics", "paid", default={}) or {})


# ───────────────────────── 1. Domain overview ─────────────────────────
with tab_domain:
    c1, c2, c3 = st.columns([4, 1.4, 1.3], vertical_alignment="bottom")
    domain = c1.text_input("Domain", placeholder="competitor.com", key="dom_in")
    depth = c2.selectbox("Keywords to pull", [100, 500, 1000], index=1, key="dom_depth",
                         help="More keywords give better intent and position data. Cost grows with this number.")
    go = c3.button("Analyze domain", type="primary", key="dom_go", width="stretch")
    if go:
        d = clean_domain(domain)
        if not d:
            st.warning("Add a domain.")
        else:
            with st.spinner(f"Pulling data for {d}..."):
                st.session_state.dom = {
                    "name": d, "loc": loc_name, "code": loc_code,
                    "rank": call("dataforseo_labs/google/domain_rank_overview/live", {**BASE, "target": d}),
                    "hist": call("dataforseo_labs/google/historical_rank_overview/live", {**BASE, "target": d}),
                    "kws": call("dataforseo_labs/google/ranked_keywords/live",
                                {**BASE, "target": d, "limit": depth,
                                 "order_by": ["ranked_serp_element.serp_item.etv,desc"]}),
                    "pages": call("dataforseo_labs/google/relevant_pages/live",
                                  {**BASE, "target": d, "limit": 100, "order_by": ["metrics.organic.etv,desc"]}),
                    "comp": call("dataforseo_labs/google/competitors_domain/live", {**BASE, "target": d, "limit": 20}),
                    "links": call("backlinks/summary/live",
                                  {"target": d, "include_subdomains": True, "rank_scale": "one_hundred"}),
                    "regions": regions_for(d),
                }

    dom = st.session_state.get("dom")
    if dom:
        organic, paid = domain_organic(dom)
        links = first(dom["links"])
        hist = hist_df(dom["hist"])
        regions = dom["regions"]
        kdf = ranked_df(dom["kws"])
        t_note, t_tone = delta(hist, "Traffic")
        k_note, k_tone = delta(hist, "Keywords")
        share = None
        if not regions.empty and regions["Est. traffic"].sum():
            share = regions.loc[regions["Code"] == dom["code"], "Est. traffic"].sum() / regions["Est. traffic"].sum()

        cards([
            ("Authority score", fmt(links.get("rank")), None, None),
            ("Organic traffic", fmt(organic.get("etv")), t_note, t_tone),
            ("Organic keywords", fmt(organic.get("count")), k_note, k_tone),
            ("Traffic share", f"{share:.0%}" if share is not None else "–", dom["loc"], "info"),
            ("Paid traffic", fmt(paid.get("etv")), None, None),
            ("Paid keywords", fmt(paid.get("count")), None, None),
            ("Referring domains", fmt(links.get("referring_domains")), None, None),
            ("Backlinks", fmt(links.get("backlinks")), None, None),
        ])

        left, right = st.columns([3, 2], gap="medium")
        with left:
            with frame("Organic traffic"):
                if hist.empty:
                    st.caption("No traffic history for this location.")
                else:
                    trend(hist, "Month", "Traffic")
        with right:
            with frame("Rankings by position"):
                bars(position_buckets(organic), "Positions", "Keywords")

        left, right = st.columns(2, gap="medium")
        with left:
            with frame("Keywords by intent"):
                idf = intent_df(kdf)
                if idf.empty:
                    st.caption("No intent data for this domain.")
                else:
                    bars(idf, "Intent", "Keywords", horizontal=True, height=200)
                    st.caption(f"Based on the top {len(kdf):,} keywords by traffic.")
        with right:
            with frame("Keywords by country"):
                if regions.empty:
                    st.caption("No country data for this domain.")
                else:
                    table(regions.drop(columns="Code").head(10))

        with frame("Top organic keywords"):
            if kdf.empty:
                st.caption("No ranking keywords found in this location.")
            else:
                table(kdf[["Keyword", "Position", "Volume", "KD", "Intent", "Est. traffic", "URL"]].head(10))
                st.caption("See every keyword in the Organic rankings tab.")

        left, right = st.columns(2, gap="medium")
        with left:
            with frame("Top pages"):
                pdf = pages_df(dom["pages"], organic.get("etv") or 0)
                if pdf.empty:
                    st.caption("No page data for this domain.")
                else:
                    table(pdf[["Page", "Est. traffic", "Traffic share"]].head(10))
        with right:
            with frame("Organic competitors"):
                cdf = competitors_df(dom["comp"], dom["name"])
                if cdf.empty:
                    st.caption("No competitors found.")
                else:
                    table(cdf[["Domain", "Shared keywords", "Their traffic"]].head(10))

        if not kdf.empty and st.button("Summarize with Claude", key="dom_ai"):
            ai_summary(f"Top ranking keywords for {dom['name']} in {dom['loc']}", kdf)

# ───────────────────────── 2. Organic rankings ─────────────────────────
with tab_rank:
    dom = st.session_state.get("dom")
    kdf = ranked_df(dom["kws"]) if dom else pd.DataFrame()
    if not dom:
        st.info("Analyze a domain in the Domain overview tab first.")
    elif kdf.empty:
        st.info("No ranking keywords found in this location.")
    else:
        f1, f2, f3, f4 = st.columns(4)
        bucket = f1.selectbox("Position", ["All"] + list(BUCKETS), key="rk_pos")
        intents = f2.multiselect("Intent", INTENTS, key="rk_int")
        min_vol = f3.number_input("Min volume", 0, value=0, step=10, key="rk_vol")
        must = f4.text_input("Must contain", placeholder="e.g. best, vs, pricing", key="rk_must")
        view = kdf[kdf["Volume"].fillna(0) >= min_vol]
        if bucket != "All":
            lo, hi = BUCKETS[bucket]
            view = view[view["Position"].between(lo, hi)]
        if intents:
            view = view[view["Intent"].isin(intents)]
        if must.strip():
            view = view[contains_any(view["Keyword"], must)]
        with frame(f"Organic rankings for {dom['name']}"):
            st.caption(f"{len(view):,} of {len(kdf):,} keywords in {dom['loc']}")
            table(view, height=560)
            download(view, f"organic-rankings-{dom['name']}")

# ───────────────────────── 3. Top pages ─────────────────────────
with tab_pages:
    dom = st.session_state.get("dom")
    if not dom:
        st.info("Analyze a domain in the Domain overview tab first.")
    else:
        organic, _ = domain_organic(dom)
        pdf = pages_df(dom["pages"], organic.get("etv") or 0)
        if pdf.empty:
            st.info("No page data for this domain in this location.")
        else:
            with frame("Traffic by page"):
                top = pdf.head(10).assign(Path=lambda x: x["Page"].map(short_path))
                bars(top, "Path", "Est. traffic", horizontal=True, height=320)
            with frame(f"Top pages for {dom['name']}"):
                st.caption(f"{len(pdf):,} pages in {dom['loc']}")
                table(pdf, height=560)
                download(pdf, f"top-pages-{dom['name']}")

# ───────────────────────── 4. Keyword overview ─────────────────────────
with tab_kw:
    raw = st.text_area("Keywords (one per line, up to 100)", placeholder="saas seo agency\nb2b seo agency", key="kw_raw")
    if st.button("Get keyword data", type="primary", key="kw_go"):
        kws = [k.strip().lower() for k in raw.splitlines() if k.strip()][:100]
        if not kws:
            st.warning("Add at least one keyword.")
        else:
            res = call("dataforseo_labs/google/keyword_overview/live", {**BASE, "keywords": kws})
            st.session_state.kw_items = (first(res).get("items") or []) if res is not None else []

    items = st.session_state.get("kw_items")
    if items:
        df = pd.DataFrame([kw_row(i) for i in items]).sort_values("Volume", ascending=False, na_position="last")
        pick = st.selectbox("Keyword details", df["Keyword"].tolist(), key="kw_pick")
        item = next(i for i in items if i.get("keyword") == pick)
        row = kw_row(item)
        comp = row["Competition"]
        cards([
            ("Volume", fmt(row["Volume"]), loc_name, "info"),
            ("Keyword difficulty", "–" if row["KD"] is None else f"{row['KD']:.0f}", row["KD level"], "info"),
            ("CPC", f"${row['CPC'] or 0:.2f}", f"{comp.capitalize()} competition" if comp else None, "info"),
            ("Intent", (row["Intent"] or "–").capitalize(), None, None),
        ])
        left, right = st.columns([3, 2], gap="medium")
        with left:
            with frame("Search trend"):
                t = trend_df(item)
                if t.empty:
                    st.caption("No monthly data for this keyword.")
                else:
                    bars(t, "Month", "Searches")
        with right:
            with frame("SERP features"):
                feats = [f for f in row["SERP features"].split(", ") if f]
                chips(feats, on=("AI Overview",))
        with frame("All keywords"):
            table(df)
            download(df, "keyword-overview")
    elif items == []:
        st.info("No data found for these keywords in this location.")

# ───────────────────────── 5. Keyword ideas ─────────────────────────
with tab_ideas:
    c1, c2, c3 = st.columns([3, 2, 1], vertical_alignment="bottom")
    seed = c1.text_input("Seed keyword", placeholder="seo agency", key="ideas_in")
    method = c2.selectbox("Match type", list(METHODS), key="ideas_method")
    limit = c3.selectbox("How many", [100, 300, 500, 1000], key="ideas_limit")
    st.caption(METHODS[method][1])
    if st.button("Find keyword ideas", type="primary", key="ideas_go"):
        s = seed.strip().lower()
        if not s:
            st.warning("Add a seed keyword.")
        else:
            res = call(METHODS[method][0], ideas_payload(method, s, limit))
            st.session_state.ideas = (first(res).get("items") or []) if res is not None else []
            st.session_state.ideas_seed = s

    ideas = st.session_state.get("ideas")
    if ideas:
        seed_s = st.session_state.get("ideas_seed", "")
        df = pd.DataFrame([kw_row(i) for i in ideas]).dropna(subset=["Keyword"]).drop_duplicates("Keyword")
        side, main = st.columns([1, 3], gap="medium")
        with side:
            with frame("Groups"):
                opts = ["All keywords"] + [f"{w} ({n})" for w, n in word_groups(df["Keyword"], seed_s)]
                group = st.radio("Group", opts, key=f"ideas_group_{seed_s}_{len(df)}", label_visibility="collapsed")
        with main:
            f1, f2, f3, f4 = st.columns(4, vertical_alignment="bottom")
            min_vol = f1.number_input("Min volume", 0, value=0, step=10, key="id_vol")
            max_kd = f2.slider("Max KD", 0, 100, 100, key="id_kd")
            intents = f3.multiselect("Intent", INTENTS, key="id_int")
            questions = f4.toggle("Questions only", key="id_q")
            g1, g2 = st.columns(2)
            must = g1.text_input("Include words", placeholder="e.g. best, vs, pricing", key="id_inc")
            skip = g2.text_input("Exclude words", placeholder="e.g. free, jobs", key="id_exc")

            view = df[(df["Volume"].fillna(0) >= min_vol) & (df["KD"].fillna(0) <= max_kd)]
            if group != "All keywords":
                word = group.rsplit(" (", 1)[0]
                view = view[view["Keyword"].str.lower().str.split().apply(lambda ws: word in ws)]
            if intents:
                view = view[view["Intent"].isin(intents)]
            if questions:
                view = view[view["Keyword"].str.lower().str.split().str[0].isin(QUESTION_WORDS)]
            if must.strip():
                view = view[contains_any(view["Keyword"], must)]
            if skip.strip():
                view = view[~contains_any(view["Keyword"], skip)]

            cards([
                ("Keywords", fmt(len(view)), None, None),
                ("Total volume", fmt(view["Volume"].sum()), None, None),
                ("Average KD", f"{view['KD'].mean():.0f}" if view["KD"].notna().any() else "–",
                 kd_label(view["KD"].mean()) if view["KD"].notna().any() else None, "info"),
            ], cols=3)
            with frame(f"Keyword ideas for {seed_s}"):
                table(view.drop(columns=["Competition"]), height=520)
                download(view, f"keyword-ideas-{seed_s.replace(' ', '-')}")
            if st.button("Summarize with Claude", key="ideas_ai"):
                ai_summary(f"Keyword ideas ({method}) for '{seed_s}' in {loc_name}", view)
    elif ideas == []:
        st.info("No ideas found. Try a broader seed keyword or another match type.")

# ───────────────────────── 6. SERP check ─────────────────────────
with tab_serp:
    c1, c2, c3 = st.columns([3, 2, 1], vertical_alignment="bottom")
    q = c1.text_input("Keyword to check", placeholder="best b2b seo agency", key="serp_q")
    track = c2.text_input("Your domain (optional)", placeholder="perceptric.com", key="serp_track")
    depth = c3.selectbox("Results", [10, 20, 50, 100], key="serp_depth")
    if st.button("Check Google results", type="primary", key="serp_go"):
        if not q.strip():
            st.warning("Add a keyword.")
        else:
            st.session_state.serp = call("serp/google/organic/live/advanced",
                                         {**BASE, "keyword": q.strip(), "depth": depth, "load_async_ai_overview": True})
            st.session_state.serp_depth_used = depth

    serp = st.session_state.get("serp")
    if serp:
        items = first(serp).get("items") or []
        t = clean_domain(track) if track.strip() else ""
        types = []
        for i in items:
            if i.get("type") != "organic" and i.get("type") not in types:
                types.append(i.get("type"))
        labels = feature_names(types)
        organic_items = [i for i in items if i.get("type") == "organic"]
        aio = next((i for i in items if i.get("type") == "ai_overview"), None)
        refs = (aio or {}).get("references") or []
        paa = next((i for i in items if i.get("type") == "people_also_ask"), None)
        mine = next((i.get("rank_group") for i in organic_items if t and t in (i.get("domain") or "")), None)
        cited = bool(t) and any(t in (r.get("domain") or "") for r in refs)
        used = st.session_state.get("serp_depth_used", depth)

        if not t:
            pos_note, pos_tone = "Add your domain", "info"
        elif mine:
            pos_note, pos_tone = None, None
        else:
            pos_note, pos_tone = f"Not in top {used}", "down"
        cards([
            ("Your position", f"#{mine}" if mine else "–", pos_note, pos_tone),
            ("AI Overview", "Yes" if aio else "No",
             ("You are cited" if cited else ("Not cited" if t else None)) if aio else None,
             "up" if cited else "down"),
            ("SERP features", str(len(labels)), None, None),
            ("Organic results", str(len(organic_items)), None, None),
        ])

        left, right = st.columns(2, gap="medium")
        with left:
            with frame("SERP features"):
                chips(labels, on=("AI Overview",))
        with right:
            with frame("People also ask"):
                qs = [x.get("title") for x in ((paa or {}).get("items") or []) if x.get("title")]
                if qs:
                    bullets(qs)
                else:
                    st.caption("No People also ask box on this search.")

        if refs:
            with frame("AI Overview sources"):
                rdf = pd.DataFrame([{"Domain": r.get("domain"), "Title": r.get("title"), "URL": r.get("url")} for r in refs])
                hl = rdf.style.apply(lambda r: ["background-color: #E8EFFC" if t and t in str(r["Domain"]) else ""
                                                for _ in r], axis=1)
                table(rdf, styler=hl)

        with frame("Results"):
            sdf = pd.DataFrame([{
                "Position": i.get("rank_absolute"),
                "Type": FEATURES.get(i.get("type"), (i.get("type") or "").replace("_", " ").capitalize()),
                "Domain": i.get("domain"), "Title": i.get("title"), "URL": i.get("url"),
            } for i in sorted(items, key=lambda x: x.get("rank_absolute") or 999)])
            hl = sdf.style.apply(lambda r: ["background-color: #E8EFFC" if t and t in str(r["Domain"]) else ""
                                            for _ in r], axis=1)
            table(sdf, styler=hl, height=520)
            download(sdf, "serp")

# ───────────────────────── 7. AI visibility ─────────────────────────
with tab_ai:
    ai_keys = {e: secret(k) for e, (k, _, _) in AI_ENGINES.items()}
    ai_models = {e: secret(m, d) for e, (_, m, d) in AI_ENGINES.items()}
    available = [e for e in AI_ENGINES if ai_keys[e]]
    if "Claude" not in available:
        st.info("Add ANTHROPIC_API_KEY to your secrets to turn on AI visibility. Claude builds the keywords.")
    else:
        c1, c2 = st.columns([4, 1.4], vertical_alignment="bottom")
        website = c1.text_input("Website", placeholder="momos.com", key="ai_site")
        if c2.button("Generate keywords", type="primary", key="ai_gen", width="stretch"):
            if not website.strip():
                st.warning("Add a website.")
            else:
                with st.spinner("Claude is reading the website and building keywords..."):
                    try:
                        st.session_state.ai_profile = generate_profile(
                            normalize_url(website), ai_keys["Claude"], ai_models["Claude"])
                        st.session_state.pop("ai_results", None)
                    except Exception as e:
                        st.error(f"Keyword generation failed: {str(e)[:300]}")

        profile = st.session_state.get("ai_profile")
        if profile:
            pid = profile["url"]
            with frame("Review before you run"):
                col1, col2 = st.columns(2)
                profile["brand"] = col1.text_input("Brand name", profile.get("brand", ""), key=f"ai_brand_{pid}")
                aliases = col2.text_input("Aliases (comma separated)", ", ".join(profile.get("aliases", [])),
                                          key=f"ai_alias_{pid}")
                profile["aliases"] = [a.strip() for a in aliases.split(",") if a.strip()]
                if profile.get("category"):
                    st.caption(profile["category"])
                kw_text = st.text_area("Keywords (one per line)", "\n".join(profile["keywords"]), height=360,
                                       key=f"ai_kws_{pid}")
                keywords = [k.strip() for k in kw_text.splitlines() if k.strip()]
                engines = st.multiselect("Engines", available, default=available, key="ai_engines")
                st.caption(f"{len(keywords)} keywords × {len(engines)} engines = {len(keywords) * len(engines)} API calls")
                run = st.button("Run visibility check", type="primary", key="ai_run",
                                disabled=not (keywords and engines))

            if run:
                jobs = [(e, k) for e in engines for k in keywords]
                results, bar = [], st.progress(0.0, text="Asking the AI engines...")
                with ThreadPoolExecutor(max_workers=AI_WORKERS) as ex:
                    futures = [ex.submit(run_one, e, k, ai_keys[e], ai_models[e], AI_WEB) for e, k in jobs]
                    for i, f in enumerate(as_completed(futures), start=1):
                        results.append(f.result())
                        bar.progress(i / len(jobs), text=f"{i} of {len(jobs)} answers received")
                bar.empty()
                terms = brand_terms(profile["brand"], profile["aliases"], profile["url"])
                st.session_state.ai_results = build_ai_frames(results, terms)

        if st.session_state.get("ai_results") and profile:
            df, comp_df = st.session_state.ai_results
            render_ai_report(profile, df, comp_df)