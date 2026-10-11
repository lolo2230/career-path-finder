"""Career Path Finder - capstone project (INDU 6993, Concordia).

Step 1: the box of jobs.
  - Job Bank open data (Government of Canada, monthly), cleaned so salary,
    education, experience, language and dates can be compared.
  - Each Job Bank job gets the official profile of its occupation (OaSIS):
    what the occupation is, its main duties, usual requirements and key skills.
  - Company job sites (Greenhouse, Lever, Ashby) are optional.
Next steps (read the CV, rank, explain, ask, update) build on this page.
"""
import re
import unicodedata

import pandas as pd
import streamlit as st

import importlib

import eligibility
import feeds
import finder
import jobbank
import oasis

# Streamlit Cloud reruns app.py after each update but can keep old copies of the helper files in memory.
# Reloading them makes sure every update is picked up without restarting the app.
for _module in (eligibility, feeds, finder, jobbank, oasis):
    importlib.reload(_module)

st.set_page_config(page_title="Career Path Finder", page_icon="🧭", layout="wide")
st.title("Career Path Finder")
st.caption("Step 1 of the build: the box of jobs · Job Bank open data + official occupation profiles (OaSIS)")

# ---- 0. about you (asked once; kept while the page is open) -------------------------
with st.sidebar:
    st.header("About you")
    st.caption("Asked once and used on every job. Nothing is saved.")
    status = st.selectbox("Your status to work in Canada", eligibility.STATUSES, key="status",
                          help="Used to flag jobs: ✅ you can apply · ⚠️ check the posting · ❌ not eligible as written.")
    hide_blocked = st.checkbox("Hide jobs I can't apply to (❌)", value=False, key="hide_blocked",
                               disabled=status == "Prefer not to say")

COLUMNS = ["title", "employer", "occupation", "noc", "city", "province", "salary", "salary_year_min",
           "salary_year_max", "language", "education", "education_level", "experience", "experience_years",
           "job_type", "posted", "posted_date", "source", "link", "description", "all_text"]
NUMERIC = ["salary_year_min", "salary_year_max", "education_level", "experience_years"]

# Bump this number whenever the way jobs are read changes, so old cached tables are not reused.
DATA_VERSION = 11


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def get_files():
    return jobbank.list_files()


@st.cache_data(ttl=24 * 3600, show_spinner=False, max_entries=2)
def get_jobbank(url, version=DATA_VERSION):
    raw = jobbank.download(url)
    jobs, mapping = jobbank.tidy(raw)
    return jobs, mapping, list(raw.columns)


@st.cache_data(ttl=30 * 24 * 3600, show_spinner=False)
def get_profiles(version=DATA_VERSION):
    return oasis.load()


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_one_company(company, version=DATA_VERSION):
    """One company's jobs, kept 6 hours. A failure raises, and failures are never kept, so they retry."""
    rows, info = feeds._one(company)
    if info["status"].startswith("busy"):
        raise RuntimeError(info["status"])
    return rows, info


def _one_or_retry_later(company):
    try:
        return get_one_company(company)
    except Exception as err:
        ats, token, name = company
        return [], {"company": name, "system": ats, "board": token, "jobs in Canada": 0, "status": str(err)}


def get_company_jobs(companies):
    return feeds.load(list(companies), fetch=_one_or_retry_later)


@st.cache_data(ttl=7 * 24 * 3600, show_spinner=False)
def get_found_companies(version=DATA_VERSION):
    return finder.find()


def complete(df):
    """Give every table the same columns so they can be stacked together."""
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = None if col in NUMERIC + ["posted_date"] else ""
    df = df[COLUMNS].copy()
    for col in NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["posted_date"] = pd.to_datetime(df["posted_date"], errors="coerce")
    text_cols = [c for c in COLUMNS if c not in NUMERIC + ["posted_date"]]
    df[text_cols] = df[text_cols].fillna("")
    return df


def pct(series):
    return f"{100 * series.mean():.0f}%" if len(series) else "–"


def clean_place(s):
    """'Montréal', 'MONTREAL ', 'Montreal, QC' -> 'Montreal', so the same city is listed once."""
    if not isinstance(s, str) or not s.strip():
        return ""
    s = s.split(",")[0].strip()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.title()


def clean_title(s):
    """'Data Analyst - FT (Montreal)' -> 'Data Analyst', so similar postings are grouped under one position."""
    if not isinstance(s, str) or not s.strip():
        return ""
    s = re.split(r"\s+[-–|/]\s+|\s*\(", s.strip())[0]
    return s.strip().title()


# ---- 1. load the data ---------------------------------------------------------
sources = st.multiselect("Job sources", ["Job Bank (government)", "Company job sites"],
                         default=["Job Bank (government)"],
                         help="Company job sites add employer names and full descriptions, but far fewer jobs.")
tables, notes = [], {}

with st.spinner("Loading official occupation profiles (OaSIS)…"):
    profiles, oasis_status = get_profiles()

if "Job Bank (government)" in sources:
    try:
        with st.spinner("Looking for the newest Job Bank file…"):
            files = get_files()
        if not files:
            raise RuntimeError("no English CSV files were listed")
        labels = [f["name"] for f in files]
        choice = st.selectbox("Job Bank month", labels, index=0,
                              help="The newest month is picked automatically. Each monthly file holds the jobs "
                                   "first posted that month.")
        with st.spinner("Downloading the Job Bank postings (the first time can take a minute)…"):
            jb, mapping, raw_cols = get_jobbank(files[labels.index(choice)]["url"])
        notes["Job Bank"] = (raw_cols, mapping)
        jb_all, _ = jobbank.combine([complete(jb.copy())])
        tables.append(jb_all)
    except Exception as err:  # the government site can be slow or down
        st.warning(f"Could not download the Job Bank file automatically ({err}). "
                   f"You can download it from the [Job Bank data page]({jobbank.DATASET_PAGE}) and upload it here.")
        up = st.file_uploader("Upload a Job Bank CSV", type="csv")
        if up:
            raw = jobbank.read_csv_bytes(up.getvalue())
            jb, mapping = jobbank.tidy(raw)
            tables.append(jobbank.combine([complete(jb)])[0])
            notes["Job Bank"] = (list(raw.columns), mapping)

company_status, found = None, None
if "Company job sites" in sources:
    st.session_state.setdefault("added", [])
    use_finder = st.checkbox(
        "Also include companies found automatically (the first search takes a few minutes, then it's kept for 7 days)",
        value=False, key="use_finder")
    companies = list(feeds.COMPANIES) + st.session_state["added"]
    if use_finder:
        with st.spinner(f"Checking {len(finder.names())} Canadian employers on 7 hiring systems… (a few minutes the first time)"):
            found = get_found_companies()
        busy = int(found["busy checks"].iloc[0]) if len(found) and "busy checks" in found.columns else 0
        if busy:
            st.caption(f"Company finder: {busy:,} of {int(found['checks'].iloc[0]):,} checks found a busy "
                       "system. Click to check again.")
            if st.button("Check again"):
                get_found_companies.clear()
                st.rerun()
        companies += [(r.system, r.board, r.company) for r in found.itertuples()]
    with st.spinner(f"Collecting jobs from {len(companies)} company job sites…"):
        cj, company_status = get_company_jobs(tuple(companies))
    tables.append(complete(cj.copy()))

if not tables:
    st.info("Choose at least one job source above.")
    st.stop()

jobs = pd.concat(tables, ignore_index=True)
if "age" not in jobs.columns:
    jobs["age"], jobs["age_days"] = "", None
jobs["age"] = jobs["age"].fillna("")
jobs["has_profile"] = jobs["noc"].isin(profiles.keys())
# Who can apply: read from the full description when we have one (company sites, later alerts and pasted jobs).
jobs["work_permit"] = [eligibility.requirement(d) if d else eligibility.JOB_BANK_NOTE for d in jobs["description"]]
fits = [eligibility.fit(status, r) for r in jobs["work_permit"]]
jobs["can_apply"] = [f[0] for f in fits]
jobs["can_apply_why"] = [f[1] for f in fits]
# The text the CV will be compared with later: the posting itself plus its occupation profile.
jobs["all_text"] = jobs["all_text"] + " · " + jobs["noc"].map(lambda n: oasis.profile_text(profiles.get(n)))

# Cleaned city and job position, used by the City and Job position filters.
jobs["city_clean"] = jobs["city"].map(clean_place)
# Some employers write the province in the city box ("Quebec", "Quebec"): treat that as no city.
jobs.loc[jobs["city_clean"].str.lower() == jobs["province"].map(clean_place).str.lower(), "city_clean"] = ""
jobs["location"] = jobs["city_clean"] + ", " + jobs["province"]
jobs["position"] = jobs["title"].map(clean_title)

# ---- 2. filters ---------------------------------------------------------------
st.subheader("Find jobs")

# Row 1: where and what. Each box only lists what is left after the box before it.
r1 = st.columns(3)
provinces = sorted(p for p in jobs["province"].unique() if p)
prov = r1[0].multiselect("Province", provinces,
                         default=[p for p in provinces if re.search(r"qu[eé]bec|^qc$", p, re.I)],
                         placeholder="All provinces")

pool = jobs[jobs["province"].isin(prov)] if prov else jobs
city_counts = pool.loc[pool["city_clean"] != "", "location"].value_counts()
cities = r1[1].multiselect("City", list(city_counts.index),
                           format_func=lambda loc: f"{loc} ({city_counts[loc]:,})",
                           placeholder="All cities in the chosen provinces",
                           help="Pick as many cities as you want. Only cities in the chosen provinces are listed; "
                                "the number is how many jobs each has. Type to search.")

if cities:
    pool = pool[pool["location"].isin(cities)]
position_counts = pool.loc[pool["position"] != "", "position"].value_counts()
positions = r1[2].multiselect("Job position", list(position_counts.index),
                              format_func=lambda p: f"{p} ({position_counts[p]:,})",
                              placeholder="All positions — type to search",
                              help="Job titles found in the chosen places, most common first. Pick as many as you want.")

# Row 2: free-text keywords and freshness
r1b = st.columns([2, 1])
words = r1b[0].text_input("Keywords in the job title or occupation",
                          placeholder="e.g. analyst, nurse, accountant, développeur")
posted_within = r1b[1].selectbox("Posted within", ["Any time", "7 days", "14 days", "30 days", "60 days", "90 days"],
                                 help="Job Bank's files have no closing date. Older jobs are more likely to be filled.")

r2 = st.columns(4)
edu_labels = ["Any", "No degree required", "Secondary (high) school", "College, CEGEP, trade or apprenticeship",
              "Bachelor's degree", "Master's degree", "Doctorate"]
my_edu = r2[0].selectbox("My highest education", edu_labels,
                         help="Hides jobs that ask for more education than this. Jobs that don't say are kept.")
my_exp = r2[1].selectbox("My years of experience", ["Any", 0, 1, 2, 3, 5, 10],
                         help="Hides jobs that ask for more experience than this. Jobs that don't say are kept.")
min_pay = r2[2].number_input("Minimum yearly pay ($)", min_value=0, max_value=300000, value=0, step=5000,
                             help="Estimated: hourly pay × 2,080 hours. Jobs without a salary are kept.")
langs = r2[3].multiselect("Language of work", ["English", "French", "English and French", "English or French"])

view = jobs
if prov:
    view = view[view["province"].isin(prov)]
if cities:
    view = view[view["location"].isin(cities)]
if positions:
    view = view[view["position"].isin(positions)]
if words.strip():
    pattern = "|".join(re.escape(w.strip()) for w in words.split(",") if w.strip())
    view = view[view["title"].str.contains(pattern, case=False, na=False, regex=True)
                | view["occupation"].str.contains(pattern, case=False, na=False, regex=True)]
if posted_within != "Any time":
    days = int(posted_within.split()[0])
    view = view[view["posted_date"].isna() | (view["posted_date"] >= pd.Timestamp.today().normalize() - pd.Timedelta(days=days))]
if my_edu != "Any":
    lvl = edu_labels.index(my_edu)
    view = view[view["education_level"].isna() | (view["education_level"] <= lvl)]
if my_exp != "Any":
    view = view[view["experience_years"].isna() | (view["experience_years"] <= my_exp)]
if min_pay:
    view = view[view["salary_year_max"].isna() | (view["salary_year_max"] >= min_pay)]
if langs:
    view = view[view["language"].isin(langs)]
if hide_blocked:
    view = view[view["can_apply"] != "❌"]

view = view.sort_values("posted_date", ascending=False, na_position="last")

m = st.columns(4)
m[0].metric("Jobs loaded", f"{len(jobs):,}")
m[1].metric("Matching your filters", f"{len(view):,}")
m[2].metric("Occupations", f"{view['noc'].replace('', pd.NA).nunique():,}")
m[3].metric("With an occupation profile", pct(view["has_profile"]))
st.caption(f"Jobs loaded: {(jobs['source'] != 'Company site').sum():,} from Job Bank · "
           f"{(jobs['source'] == 'Company site').sum():,} from company job sites")

# ---- 3. the table, with a click to see details ------------------------------------
view = view.assign(occupation_link=view["noc"].map(oasis.occupation_url))
table = view[["can_apply", "title", "employer", "occupation", "occupation_link", "city", "province", "salary", "salary_year_min",
              "salary_year_max", "language", "education", "experience", "work_permit", "job_type", "posted", "age",
              "source", "link"]].head(2000)
if status == "Prefer not to say":
    table = table.drop(columns="can_apply")
st.info("👉 Tick the small box at the left of a row to open that job's details and its occupation profile below the table. "
        "The **View** link opens the official occupation page on the Government of Canada website.")
event = st.dataframe(
    table, width="stretch", hide_index=True, on_select="rerun", selection_mode="single-row", key="jobs_table",
    column_config={
        "can_apply": st.column_config.TextColumn("Can I apply?", width="small",
                                                 help="Based on your status in the sidebar"),
        "work_permit": "Who can apply",
        "title": "Job title", "employer": "Employer", "occupation": "Occupation (NOC)",
        "city": "City or region", "province": "Province", "salary": "Salary (as posted)",
        "salary_year_min": st.column_config.NumberColumn("Yearly from (est.)", format="$%d"),
        "salary_year_max": st.column_config.NumberColumn("Yearly to (est.)", format="$%d"),
        "language": "Language", "education": "Education", "experience": "Experience",
        "job_type": "Type", "posted": "Posted", "age": "How old", "source": "Source",
        "link": st.column_config.LinkColumn("Job posting", display_text="Open"),
        "occupation_link": st.column_config.LinkColumn("Occupation details", display_text="View",
                                                       help="Official Government of Canada page for this occupation"),
    },
)
if len(view) > 2000:
    st.caption("Showing the 2,000 newest matches. Add a keyword or a filter to narrow the list.")

rows = event.selection.rows if event and event.selection else []
if rows:
    job = table.iloc[rows[0]]
    full = view.loc[table.index[rows[0]]]
    st.subheader(job["title"].capitalize())
    left, right = st.columns([1, 2])
    with left:
        st.markdown("**What the posting says**")
        facts = {
            "Employer": job["employer"] or "Not given in Job Bank's open data",
            "Occupation": f"{job['occupation']} (NOC {full['noc']})" if full["noc"] else job["occupation"],
            "Where": ", ".join(x for x in [job["city"], job["province"]] if x) or "Not given",
            "Salary": job["salary"] or "Not given",
            "Education": job["education"] or "Not given",
            "Experience": job["experience"] or "Not given",
            "Language": job["language"] or "Not given",
            "Type": job["job_type"] or "Not given",
            "Posted": (job["posted"] + (f" ({full['age']})" if full["age"] else "")) or "Not given",
            "Who can apply": full["work_permit"],
        }
        for k, v in facts.items():
            st.markdown(f"- **{k}:** {v}")
        if full["can_apply"]:
            st.markdown(f"**Can I apply?** {full['can_apply']} {full['can_apply_why']}")
        if job["link"]:
            st.link_button("Open on " + ("the company site" if job["source"] == "Company site" else "Job Bank"),
                           job["link"])
        if full["noc"]:
            st.link_button("Occupation details (Government of Canada)", oasis.occupation_url(full["noc"]))
    with right:
        if full["description"]:
            st.markdown("**Job description** 🟢 *from the employer's posting*")
            st.write(full["description"][:3000])
        p = profiles.get(full["noc"])
        if p:
            st.markdown("**Typical duties and skills for this occupation** 🟡 "
                        "*official profile from the Government of Canada (OaSIS), not this employer's posting*")
            if p["lead"]:
                st.write(p["lead"])
            if p["duties"]:
                st.markdown("*Main duties*\n" + "\n".join(f"- {d}" for d in p["duties"]))
            if p["skills"]:
                st.markdown("*Most important skills:* " + ", ".join(p["skills"]))
            if p["requirements"]:
                st.markdown("*Usual requirements*\n" + "\n".join(f"- {r}" for r in p["requirements"]))
        elif not full["description"]:
            st.info("No description or occupation profile is available for this job.")

# ---- 3b. check any job description ----------------------------------------------
with st.expander("Check a job description: who can apply? (paste any job ad)"):
    pasted = st.text_area("Paste the full job description here (English or French)", height=150, key="pasted")
    if pasted.strip():
        req = eligibility.requirement(pasted)
        st.markdown(f"**What the job says:** {req}")
        icon, why = eligibility.fit(status, req)
        if icon:
            st.markdown(f"**For you ({status}):** {icon} {why}")
        else:
            st.caption("Choose your status in the sidebar to see whether you can apply.")

# ---- 3c. add a company ----------------------------------------------------------
if "Company job sites" in sources:
    with st.expander("Add a company (paste its careers page link)"):
        url = st.text_input("Careers page link", placeholder="e.g. https://jobs.lever.co/eqbank or https://apply.workable.com/acme",
                            key="new_company")
        if url.strip():
            hit = feeds.from_url(url)
            if not hit:
                st.warning("This link isn't from one of the 7 open hiring systems we can read "
                           "(Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, BambooHR).")
            else:
                rows, info = feeds._one(hit, details=False)
                st.write(f"Found **{info['system']}** board **{info['board']}**: {info['status']}, "
                         f"**{info['jobs in Canada']}** jobs in Canada.")
                if info["status"] == "ok" and hit not in st.session_state["added"]:
                    if st.button("Add it to the job list"):
                        st.session_state["added"].append(hit)
                        st.rerun()
        if st.session_state["added"]:
            st.caption("Added for this visit: " + ", ".join(f"{n} ({a})" for a, _, n in st.session_state["added"])
                       + ". To keep a company permanently, send the link to whoever maintains `feeds.py`.")

# ---- 4. data quality and sources (for the team and the report) -------------------
with st.expander("Data quality: how complete is the information? (for the team and the report)"):
    jbv = jobs[jobs["source"] != "Company site"]
    if len(jbv):
        quality = pd.DataFrame({
            "Information": ["Occupation profile (OaSIS)", "Salary", "Education", "Experience", "Language",
                            "City or region", "Employer name", "Job description"],
            "Share of Job Bank jobs that have it": [
                pct(jbv["has_profile"]), pct(jbv["salary_year_min"].notna()), pct(jbv["education_level"].notna()),
                pct(jbv["experience_years"].notna()), pct(jbv["language"] != ""), pct(jbv["city"] != ""),
                pct(jbv["employer"] != ""), pct(jbv["description"] != "")],
        })
        st.write(f"Job Bank file: **{len(jbv):,} postings**.")
        d = jbv["posted_date"].dropna()
        if len(d):
            st.markdown(f"**When were these jobs first posted?** Oldest: **{d.min():%Y-%m-%d}** · "
                        f"newest: **{d.max():%Y-%m-%d}** · {len(d):,} postings have a date.")
            by_month = d.dt.to_period("M").astype(str).value_counts().sort_index()
            st.bar_chart(by_month.rename("Postings"), x_label="Month first posted", y_label="Postings")
            st.caption("If every posting falls in the file's own month, the file holds only the jobs *posted that "
                       "month*. If there are earlier months too, it holds jobs that were still open.")
        st.dataframe(quality, hide_index=True, width="stretch")
        st.caption("Yearly pay is an estimate: hourly × 2,080 hours (40 h × 52 weeks), weekly × 52, monthly × 12.")
    st.write("**Occupation profiles (OaSIS 2025):**", f"{len(profiles):,} occupations loaded.")
    st.dataframe(oasis_status, hide_index=True, width="stretch")
    st.markdown(f"Sources: [Job Bank open data]({jobbank.DATASET_PAGE}) · [OaSIS]({oasis.DATASET_PAGE}) "
                "· Open Government Licence – Canada.")

with st.expander("Where do the jobs come from? (for the team)"):
    if company_status is not None:
        st.write("**Company job sites** (only jobs located in Canada are kept). Systems read: "
                 "Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Recruitee, BambooHR.")
        by_system = company_status.groupby("system").agg(companies=("company", "count"),
                                                         jobs_in_canada=("jobs in Canada", "sum"))
        st.dataframe(by_system, width="stretch")
        st.dataframe(company_status, hide_index=True, width="stretch")
        if found is not None:
            st.caption(f"Company finder: {len(found)} companies found automatically. They were matched by name, "
                       "so check the list for look-alike names before relying on them.")
    if "Job Bank" in notes:
        raw_cols, mapping = notes["Job Bank"]
        st.write("**Job Bank file** – columns found:", raw_cols)
        st.write("How the app reads them:", {k: v or "not found" for k, v in mapping.items()})
