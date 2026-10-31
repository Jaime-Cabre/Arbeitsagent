#!/usr/bin/env python3
"""
Weekly job agent: pulls manager / senior manager / director postings in analytics, business operations,
finance and project/program management from the last 7 days, then ranks them on remote status,
company size and industry.

  python job_agent.py             live run (internet needed)
  python job_agent.py --sample    offline demo on fictional postings
  python job_agent.py --new-only  list only postings not shown in earlier runs
  python job_agent.py --serve     open job-match-board.html locally; press "Run agent now" in the page to run the agent and load results

Defaults set in config.json: 30-day window, smaller companies score higher ("size_preference": "small"), politics & political consulting included.
Sources (public APIs; no scraping of LinkedIn or Indeed): Greenhouse / Lever / Ashby boards for the companies
in config.json, Remotive (remote roles) and Adzuna (broad aggregator; free key: export ADZUNA_APP_ID=... ADZUNA_APP_KEY=...).
Company size/industry come from companies.csv (columns: company, industry, approx_employees, optional approx_revenue_usd_m used when headcount is blank; approximate, edit freely); unknown companies get neutral scores.
Resume match: put your resume (resume.docx or resume.txt, or set "resume_path" in config.json) next to this file. Every posting then gets an
ATS-style match score (0-100: keyword coverage, function fit, years of experience, people-leadership ask) plus the keywords the posting
uses that your resume lacks. Add those keywords only where they are true. The score is a proxy: real ATS products differ.
Optional email: set SMTP_HOST, SMTP_USER, SMTP_PASS, MAIL_TO.
Schedule: cron `0 12 * * 1 cd /path && python job_agent.py` or the included GitHub Actions workflow.
"""
import argparse, csv, json, os, re, smtplib, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).parent
CFG = json.loads((HERE / "config.json").read_text())
OUT, SEEN = HERE / "output", HERE / "output" / "seen.json"

LEVEL = re.compile(r"\b(senior manager|sr\.? manager|associate director|director|manager|head of)\b", re.I)
EXCLUDE = re.compile(r"assistant|store manager|account manager|sales manager|product manager|engineering manager|software|district manager|territory|nurse|intern\b|vice president|\bvp\b|chief|recruit", re.I)
FUNCS = {
    "analytics": r"analytic|insight|business intelligence|\bbi\b|data science|decision science|reporting|measurement|data director|data (?:and|&) (?:targeting|strategy)|research director|polling",
    "business operations": r"business operations|biz ?ops|strategy (?:and|&) operations|operations (?:strategy|excellence|analytics)|revenue operations|sales operations|commercial operations|supply chain analytics",
    "finance": r"fp&a|financial planning|finance|financial analy",
    "project/program": r"program manage|project manage|\bpmo\b|program director|project director|transformation",
}
NON_US = re.compile(r"\b(canada|uk|united kingdom|india|germany|france|ireland|australia|singapore|emea|apac|mexico|brazil)\b", re.I)
INFER = {
    "airlines & travel": r"airline|aviation|travel|hospitality", "logistics & supply chain": r"logistic|supply chain|freight|shipping|warehouse",
    "retail & e-commerce": r"retail|e-?commerce|merchandis", "enterprise software & saas": r"saas|enterprise software|cloud platform",
    "financial services": r"\bbank|insurance|asset management|fintech|financial services", "healthcare": r"health|hospital|pharma|clinical",
    "politics & political consulting": r"political|voter|electoral|congressional|\bgotv\b|ngp van|public affairs|campaign (?:data|field|committee)",
    "staffing & recruiting": r"staffing|recruit|talent solutions|on behalf of our client",
}

def norm(n): return re.sub(r"[,.]|\s+(inc|llc|corp|corporation|co|ltd|company)$", "", n.lower()).strip()
COMP = {norm(r["company"]): r for r in csv.DictReader(open(HERE / "companies.csv", encoding="utf-8"))}

# ---- resume / ATS match -------------------------------------------------------------------------------------------------------------
import zipfile, html
LEX = {  # term: (regex, weight). Edit freely.
 "sql": (r"\bsql\b", 3), "python": (r"\bpython\b", 2), "tableau": (r"tableau", 2), "power bi": (r"power\s?bi", 2), "looker": (r"looker", 1),
 "excel": (r"\bexcel\b", 1), "dbt": (r"\bdbt\b", 1), "snowflake": (r"snowflake", 2), "databricks": (r"databricks", 1), "bigquery": (r"bigquery", 1),
 "aws": (r"\baws\b", 1), "sas": (r"\bsas\b", 1), "airflow/etl": (r"airflow|\betl\b|fivetran|matillion", 1), "data modeling": (r"data model", 2),
 "ai/ml": (r"\bai\b|artificial intelligence|machine learning|genai|generative ai", 2), "experimentation": (r"a/b test|experimentation", 1),
 "forecasting": (r"forecast", 2), "predictive": (r"predictive|prescriptive", 1), "statistical analysis": (r"statistic", 2), "kpi": (r"\bkpis?\b|key performance", 2),
 "dashboards": (r"dashboard", 2), "reporting": (r"reporting", 1), "business intelligence": (r"\bbi\b|business intelligence", 2),
 "data governance": (r"data governance|data quality", 1), "self-service": (r"self-service", 1), "revenue analytics": (r"revenue analytic|revenue operations|revops", 2),
 "sales analytics": (r"sales analytic|sales operations|go-to-market|\bgtm\b|commercial", 2), "supply chain": (r"supply chain|logistics", 2),
 "process improvement": (r"process improvement|continuous improvement|six sigma|\blean\b", 2), "automation": (r"automat", 1),
 "fp&a": (r"fp&a|financial planning|budget|\bp&l\b", 2), "pricing": (r"pricing", 1), "strategy": (r"strategy|strategic", 1),
 "executive": (r"executive|c-suite|senior leadership", 1), "stakeholder": (r"stakeholder", 2), "cross-functional": (r"cross-functional", 2),
 "change management": (r"change management", 2), "training": (r"training|enablement", 1), "mentoring": (r"mentor|coach", 1),
 "team leadership": (r"team lead|people leadership|people management|direct reports|manage a team|lead a team", 2),
 "program management": (r"program manage", 3), "project management": (r"project manage", 3), "pmo": (r"\bpmo\b|portfolio manage", 2),
 "pmp": (r"\bpmp\b", 2), "agile": (r"agile|scrum", 2), "roadmap": (r"roadmap", 1), "risk management": (r"risk (?:management|mitigation)", 1),
 "jira/confluence": (r"jira|confluence", 1), "vendor management": (r"vendor|supplier", 1), "customer success": (r"customer success", 1),
 "voter data / political": (r"voter file|ngp van|votebuilder|voter|political|polling|electoral|targeting", 2), "campaign": (r"\bcampaign", 1),
}
FN_FIT = {"analytics": 1.0, "business operations": 0.85, "project/program": 0.85, "finance": 0.45}  # how strongly your background supports each track
LEAD_ASK = re.compile(r"direct reports|people (?:management|leadership)|manage(?:s|d)? (?:a )?team|performance management|hire and develop|coach and develop", re.I)

def load_resume():
    p = CFG.get("resume_path")
    for c in ([HERE / p] if p else []) + [HERE / "resume.docx", HERE / "resume.txt"]:
        if c.exists():
            if c.suffix.lower() == ".docx":
                x = zipfile.ZipFile(c).read("word/document.xml").decode("utf8")
                return html.unescape(re.sub(r"<[^>]+>", " ", x.replace("</w:p>", "\n"))).lower()
            return c.read_text(encoding="utf8").lower()
    print("  no resume found (resume.docx / resume.txt): ATS match disabled", file=sys.stderr); return None
RESUME = load_resume()
RES_TERMS = {k for k, (rx, w) in LEX.items() if RESUME and re.search(rx, RESUME)}

def ats_match(p, fn):
    """Returns (score 0-100, confidence, missing keywords). Proxy for ATS keyword screening, not any vendor's exact algorithm."""
    text = (p["title"] + " " + re.sub(r"<[^>]+>", " ", p.get("text", ""))).lower()
    words = len(text.split()); jt = {k for k, (rx, w) in LEX.items() if re.search(rx, text)}
    tot = sum(LEX[k][1] for k in jt)
    cov = sum(LEX[k][1] for k in jt & RES_TERMS) / tot if tot else 0.6
    yrs = [int(a) for a in re.findall(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?\+?\s*years", text)]
    need = max(yrs) if yrs else None
    ys = 0.85 if need is None else min(1.0, CFG.get("resume_years", 18) / need)
    ls = 0.6 if LEAD_ASK.search(text) and not CFG.get("direct_reports") else 1.0
    score = 100 * (0.65 * cov + 0.175 * ys + 0.175 * ls)   # function fit is now its own score (see function_fit)
    conf = "high" if words >= 250 else "medium" if words >= 80 else "low"
    if conf == "low": score = 0.5 * score + 0.5 * 55   # short snippet (e.g. Adzuna): pull toward neutral
    miss = sorted(jt - RES_TERMS, key=lambda k: -LEX[k][1])[:5]
    return round(score), conf, miss

def row_size(row):
    """Employees from companies.csv: approx_employees if given, else approx_revenue_usd_m x 4 (assumes ~$250k revenue per employee)."""
    if not row: return None
    try:
        if (row.get("approx_employees") or "").strip(): return int(float(row["approx_employees"]))
        if (row.get("approx_revenue_usd_m") or "").strip(): return round(float(row["approx_revenue_usd_m"]) * 4)
    except ValueError: pass
    return None

def _amt(v):
    v = v.replace(",", "").strip().lower()
    return float(v[:-1]) * 1000 if v.endswith("k") else float(v)

SAL = re.compile(r"\$\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK])\s*(?:-|\u2013|\u2014|to)\s*\$?\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK])")
def fmt_salary(lo, hi, est=False):
    try: lo, hi = float(lo), float(hi)
    except (TypeError, ValueError): return ""
    if lo < 30000 or hi < lo: return ""   # skips hourly rates and junk
    return f"${lo:,.0f}-${hi:,.0f}" + (" (est.)" if est else "")
def salary_from_text(text):
    m = SAL.search(re.sub(r"<[^>]+>", " ", html.unescape(text or "")))
    return fmt_salary(_amt(m.group(1)), _amt(m.group(2))) if m else ""

def salary_bounds(sal):
    """(low, high) annual dollars from a salary string like '$150,000-$190,000', or None if no salary is listed."""
    nums = [float(x.replace(",", "")) for x in re.findall(r"\$\s?([\d,]{5,})", sal or "")]
    return (min(nums), max(nums)) if nums else None

def salary_ok(sal):
    """Config min_salary / max_salary (0 = off). A job passes if its listed range overlaps yours; unlisted salaries pass unless include_unlisted_salary is false."""
    lo, hi = CFG.get("min_salary", 0), CFG.get("max_salary", 0)
    b = salary_bounds(sal)
    if not b: return CFG.get("include_unlisted_salary", True) or not (lo or hi)
    return (not lo or b[1] >= lo) and (not hi or b[0] <= hi)

# Companies to never show (regexes, case-insensitive, matched against the posting's company name). Set in config.json: "exclude_companies".
EXCL = [re.compile(x, re.I) for x in CFG.get("exclude_companies", [])]

def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "job-agent/1.0"}), timeout=25) as r:
        return json.load(r)

def dt(v):
    if v in (None, ""): return None
    try:
        d = datetime.fromtimestamp(v / 1000, timezone.utc) if isinstance(v, (int, float)) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception: return None

def from_greenhouse(tok, name):
    for j in get(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs?content=true").get("jobs", []):
        yield dict(title=j["title"], company=name, location=(j.get("location") or {}).get("name", ""), url=j["absolute_url"],
                   posted=dt(j.get("first_published") or j.get("updated_at")), text=j.get("content", ""),
                   salary=next((fmt_salary(r["min_cents"] / 100, r["max_cents"] / 100) for r in j.get("pay_input_ranges") or [] if r.get("min_cents") and r.get("max_cents")), ""))

def from_lever(tok, name):
    for j in get(f"https://api.lever.co/v0/postings/{tok}?mode=json"):
        yield dict(title=j["text"], company=name, location=(j.get("categories") or {}).get("location", ""), url=j["hostedUrl"],
                   posted=dt(j.get("createdAt")), text=j.get("descriptionPlain", ""),
                   mode=(j.get("workplaceType") or "").replace("on-site", "onsite") or None,
                   salary=fmt_salary((j.get("salaryRange") or {}).get("min"), (j.get("salaryRange") or {}).get("max")))

def from_ashby(tok, name):
    for j in get(f"https://api.ashbyhq.com/posting-api/job-board/{tok}").get("jobs", []):
        yield dict(title=j["title"], company=name, location=j.get("location", ""), url=j["jobUrl"], posted=dt(j.get("publishedAt")),
                   text=j.get("descriptionPlain", ""), mode="remote" if j.get("isRemote") else None,
                   salary=(j.get("compensation") or {}).get("compensationTierSummary", ""))

def from_remotive():
    for j in get("https://remotive.com/api/remote-jobs").get("jobs", []):   # single request; rank() keeps only roles matching a function
        yield dict(title=j["title"], company=j["company_name"], location=j.get("candidate_required_location", ""), url=j["url"],
                   posted=dt(j.get("publication_date")), text=j.get("description", ""), mode="remote", src="feed")

def from_remoteok():
    for j in get("https://remoteok.com/api"):
        if not isinstance(j, dict) or not j.get("position"): continue   # first item is a legal notice
        yield dict(title=j["position"], company=j.get("company", ""), location=j.get("location") or "Remote", url=j.get("url", ""), posted=dt(j.get("date")),
                   text=j.get("description", ""), mode="remote", src="feed", salary=fmt_salary(j.get("salary_min"), j.get("salary_max")))

def from_jobicy():
    for j in get("https://jobicy.com/api/v2/remote-jobs?count=100&geo=usa").get("jobs", []):
        usd = (j.get("salaryCurrency") or "USD") == "USD"
        yield dict(title=j["jobTitle"], company=j.get("companyName", ""), location=j.get("jobGeo") or "USA", url=j["url"], posted=dt(j.get("pubDate")),
                   text=j.get("jobDescription", ""), mode="remote", src="feed", salary=fmt_salary(j.get("annualSalaryMin"), j.get("annualSalaryMax")) if usd else "")

def from_adzuna():
    app, key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (app and key): print("  adzuna skipped (no ADZUNA_APP_ID / ADZUNA_APP_KEY)", file=sys.stderr); return
    for q in CFG["search_terms"]:
        qs = urllib.parse.urlencode(dict(app_id=app, app_key=key, what=q, max_days_old=CFG["max_age_days"], results_per_page=50, sort_by="date",
                                **({"salary_min": CFG["min_salary"], "salary_include_unknown": int(CFG.get("include_unlisted_salary", True))} if CFG.get("min_salary") else {})))
        for j in get(f"https://api.adzuna.com/v1/api/jobs/us/search/1?{qs}").get("results", []):
            yield dict(title=j["title"], company=(j.get("company") or {}).get("display_name", ""), location=(j.get("location") or {}).get("display_name", ""),
                       url=j["redirect_url"], posted=dt(j.get("created")), text=j.get("description", ""),
                       salary=fmt_salary(j.get("salary_min"), j.get("salary_max"), est=str(j.get("salary_is_predicted")) == "1"))

def collect():
    jobs = []
    def run(label, gen):
        try: jobs.extend(gen)
        except Exception as e: print(f"  skipped {label}: {e}", file=sys.stderr)
    for tok, name in CFG["greenhouse"].items(): run("greenhouse:" + tok, from_greenhouse(tok, name))
    for tok, name in CFG["lever"].items(): run("lever:" + tok, from_lever(tok, name))
    for tok, name in CFG["ashby"].items(): run("ashby:" + tok, from_ashby(tok, name))
    run("remotive", from_remotive()); run("remoteok", from_remoteok()); run("jobicy", from_jobicy()); run("adzuna", from_adzuna())
    return jobs

def classify(p):
    """Level/exclusion check only. Function is no longer a filter: it is scored by function_fit()."""
    t = p["title"]
    return bool(LEVEL.search(t)) and not EXCLUDE.search(t)

def function_fit(p):
    """(function name, 0-1 score). Title match scores fully; a match only in the posting text scores 60%; no match scores 0.2."""
    body = re.sub(r"<[^>]+>", " ", p.get("text", ""))[:1500]
    for k, rx in FUNCS.items():
        if re.search(rx, p["title"], re.I): return k, FN_FIT[k]
    for k, rx in FUNCS.items():
        if re.search(rx, body, re.I): return k, round(0.6 * FN_FIT[k], 2)
    return "other", 0.2

def level(t):
    t = t.lower()
    return "Director" if re.search(r"director|head of", t) else "Senior Manager" if re.search(r"senior manager|sr\.? manager", t) else "Manager"

def work_mode(p):
    if p.get("mode") in ("remote", "hybrid", "onsite"): return p["mode"]
    loc = (p["title"] + " " + p["location"]).lower()
    blob = loc + " " + re.sub(r"<[^>]+>", " ", p.get("text", "")).lower()[:2500]
    if "hybrid" in loc: return "hybrid"
    if "remote" in loc: return "remote"
    if re.search(r"fully remote|100% remote|remote[- ]first|work from anywhere|work from home", blob): return "remote"
    if "hybrid" in blob: return "hybrid"
    if re.search(r"on-?site|in[- ]office", blob): return "onsite"
    return "unknown"

def remote_score(mode, loc):
    atl = bool(re.search(r"atlanta|, ga\b|georgia", loc, re.I))
    return {"remote": 1.0, "hybrid": 0.7 if atl else 0.45, "onsite": 0.4 if atl else 0.15, "unknown": 0.3}[mode]

def size_score(n):
    if n is None: return CFG.get("unknown_size_score", 0.4)
    s = 1.0 if n >= 10000 else 0.85 if n >= 5000 else 0.65 if n >= 1000 else 0.4 if n >= 200 else 0.2
    return s if CFG["size_preference"] == "large" else round(1.15 - s, 2)

def industry_of(p, row):
    if p.get("industry"): return p["industry"]
    if row: return row["industry"]
    blob = re.sub(r"<[^>]+>", " ", p.get("text", "")).lower()[:3000]
    return next((k for k, rx in INFER.items() if re.search(rx, blob)), "unknown")

def rank(jobs, new_only=False):
    cutoff = datetime.now(timezone.utc) - timedelta(days=CFG["max_age_days"])
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    w, out, urls = dict(CFG["weights"]), [], set()
    w.setdefault("fit", 30 if RESUME else 0); w.setdefault("function", 15)
    for p in jobs:
        if any(rx.search(p["company"]) for rx in EXCL): continue
        if not classify(p) or p["url"] in urls or (p["posted"] and p["posted"] < cutoff): continue
        if NON_US.search(p["location"]) and not re.search(r"united states|\bus\b|usa", p["location"], re.I): continue
        if new_only and p["url"] in seen: continue
        if not salary_ok(p.get("salary") or salary_from_text(p.get("text", ""))): continue
        fn, fs = function_fit(p)
        if p.get("src") == "feed" and fn == "other": continue   # broad remote feeds: keep only roles that match a function
        urls.add(p["url"])
        row = COMP.get(norm(p["company"]))
        n = p.get("employees") or row_size(row)
        mode, ind = work_mode(p), industry_of(p, row)
        r, s = remote_score(mode, p["location"]), size_score(n)
        i = CFG["industry_affinity"].get(ind, CFG["industry_affinity"]["default"])
        ats, conf, miss = ats_match(p, fn) if RESUME else (None, "n/a", [])
        score = round(100 * (w["remote"] * r + w["size"] * s + w["industry"] * i + w["fit"] * ((ats or 0) / 100) + w["function"] * fs) / sum(w.values()))
        out.append(dict(score=score, location=p["location"], ats_match=ats if ats is not None else "", ats_confidence=conf, missing_keywords="; ".join(miss), title=p["title"], company=p["company"], level=level(p["title"]), function=fn, function_pts=round(fs, 2), mode=mode,
                        employees=n or "unknown", industry=ind, posted=p["posted"].date().isoformat() if p["posted"] else "date unknown",
                        salary=p.get("salary") or salary_from_text(p.get("text", "")) or "not listed",
                        remote_pts=round(r, 2), size_pts=round(s, 2), industry_pts=round(i, 2), url=p["url"]))
    out.sort(key=lambda x: (-x["score"], x["posted"]), reverse=False)
    SEEN.parent.mkdir(exist_ok=True); SEEN.write_text(json.dumps(sorted(seen | urls)))
    return out

def report(res):
    today = datetime.now().date().isoformat()
    lines = [f"# Job matches, week of {today}", "", f"{len(res)} postings (manager / senior manager / director; posted in the last {CFG['max_age_days']} days). "
             f"Score = remote {CFG['weights']['remote']} + company size {CFG['weights']['size']} + industry {CFG['weights']['industry']} + resume/ATS fit {CFG['weights'].get('fit', 30 if RESUME else 0)} + function fit {CFG['weights'].get('function', 15)} (relative weights). Function is scored, not filtered. "
             "ATS match is a keyword proxy; 'low' confidence means the posting text was short, so open it and check.", "",
             "| # | Score | ATS match | Title (link) | Company | Salary | Level | Function | Mode | Employees | Industry | Posted | Keywords to add if true |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, r in enumerate(res, 1):
        lines.append(f"| {k} | {r['score']} | {r['ats_match']} ({r['ats_confidence']}) | [{r['title']}]({r['url']}) | {r['company']} | {r['salary']} | {r['level']} | {r['function']} ({r['function_pts']}) | {r['mode']} | {r['employees']} | {r['industry']} | {r['posted']} | {r['missing_keywords']} |")
    OUT.mkdir(exist_ok=True); text = "\n".join(lines)
    (OUT / f"jobs_{today}.md").write_text(text, encoding="utf-8")
    with open(OUT / f"jobs_{today}.csv", "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(res[0]) if res else ["score"]); wr.writeheader(); wr.writerows(res)
    return text

def write_json(res):
    """docs/jobs.json: what the GitHub Pages board (docs/index.html) loads."""
    d = HERE / "docs"; d.mkdir(exist_ok=True)
    w = {**CFG["weights"]}; w.setdefault("function", 15)
    meta = dict(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), max_age_days=CFG["max_age_days"], weights=w,
                size_preference=CFG["size_preference"], count=len(res))
    (d / "jobs.json").write_text(json.dumps(dict(meta=meta, jobs=res), default=str), encoding="utf-8")

def email(body):
    if not all(os.getenv(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_TO")): return
    m = EmailMessage(); m["Subject"], m["From"], m["To"] = "Weekly job matches", os.environ["SMTP_USER"], os.environ["MAIL_TO"]; m.set_content(body)
    with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], 465) as s: s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"]); s.send_message(m)

def serve(use_sample, port):
    """Local web app: serves job-match-board.html and runs this agent when its "Run agent now" button is pressed."""
    import http.server, webbrowser
    page = HERE / "job-match-board.html"
    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            b = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def log_message(self, *a): pass
        def do_GET(self):
            if self.path in ("/", "/index.html"): self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/ping": self._send(200, "{}")
            else: self._send(404, "not found", "text/plain")
        def do_POST(self):
            if self.path != "/api/run" or self.headers.get("X-Requested-With") != "board": return self._send(403, "forbidden", "text/plain")
            try:
                res = rank(sample() if use_sample else collect(), False); report(res)
                self._send(200, json.dumps(res, default=str))
            except Exception as e: self._send(500, f"{type(e).__name__}: {e}", "text/plain")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    url = f"http://127.0.0.1:{port}/"; print(f"Job match board running at {url} (Ctrl+C to stop)"); webbrowser.open(url)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass

def sample():
    now = datetime.now(timezone.utc); d = lambda n: now - timedelta(days=n)
    return [
        dict(title="Director, Business Analytics", company="Northwind Air (demo)", location="Remote - US", salary="$185,000-$230,000", url="demo/1", posted=d(2), text="Lead business analytics: SQL, Tableau, KPI dashboards, forecasting, 10+ years, stakeholder management, Power BI, Snowflake, manage a team of analysts. " * 6, employees=40000, industry="airlines & travel"),
        dict(title="Senior Manager, FP&A", company="Contoso Retail (demo)", location="Atlanta, GA", url="demo/2", posted=d(3), text="Hybrid, 3 days in office", employees=12000, industry="retail & e-commerce"),
        dict(title="Manager, Program Management", company="Tiny Startup (demo)", location="Remote", url="demo/3", posted=d(1), text="", employees=80, industry="enterprise software & saas"),
        dict(title="Director of Business Operations", company="Fabrikam Logistics (demo)", location="Chicago, IL", url="demo/4", posted=d(5), text="On-site", employees=9000, industry="logistics & supply chain"),
        dict(title="Senior Manager, Data Analytics", company="Staffing Partners (demo)", location="Remote", url="demo/5", posted=d(2), text="", employees=500, industry="staffing & recruiting"),
        dict(title="Data Director", company="Voter Data Group (demo)", location="Remote", url="demo/8", posted=d(2), text="Manage data team staff, voter file and NGP VAN targeting, SQL, Tableau, data quality audits, reporting across congressional districts. " * 5, employees=45, industry="politics & political consulting"),
        dict(title="Account Manager", company="Excluded (demo)", location="Remote", url="demo/6", posted=d(1), text=""),
        dict(title="Director, Analytics", company="Too Old (demo)", location="Remote", url="demo/7", posted=d(20), text="", employees=30000),
    ]

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--sample", action="store_true"); ap.add_argument("--new-only", action="store_true")
    ap.add_argument("--serve", action="store_true", help="open the HTML board locally; its Run agent now button runs this agent"); ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    if a.serve: serve(a.sample, a.port); sys.exit()
    res = rank(sample() if a.sample else collect(), a.new_only)
    body = report(res); write_json(res); print(body); email(body)
