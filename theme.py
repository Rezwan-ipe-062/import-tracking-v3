"""Anchor design tokens and the themed visual shell.

Single source of truth for colour, severity semantics, spacing, elevation and
the injected CSS. Two full themes (light / dark) are defined as token dicts
and re-injected on every rerun, so switching is a single follow-up state change
plus a rerun - no JS handshake needed.

Kept as plain Python so Streamlit can inline it without a build step, and so
every colour/radius/shadow stays driven by one token set instead of raw hex.
"""

import base64
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

# --------------------------------------------------------------------------- #
# Severity metadata -> semantic pill/dot CSS class names (resolved per theme).
# --------------------------------------------------------------------------- #
PRIORITY_ORDER = ["Critical", "Urgent", "Data Review", "Monitor"]

# value -> (fg var, bg var, dot var). Kept so callers can look up any value.
SEVERITY = {
    "Critical":    ("--a-crit-fg", "--a-crit-bg", "--a-crit-dot"),
    "Urgent":      ("--a-urg-fg",  "--a-urg-bg",  "--a-urg-dot"),
    "Monitor":     ("--a-mon-fg",  "--a-mon-bg",  "--a-mon-dot"),
    "Data Review": ("--a-data-fg", "--a-data-bg", "--a-data-dot"),
}

# --------------------------------------------------------------------------- #
# Light theme
# --------------------------------------------------------------------------- #
LIGHT = {
    "--a-bg":           "#F1F5F9",
    "--a-bg-acc":       "#E7EEF8",
    "--a-surface":      "#FFFFFF",
    "--a-surface-2":    "#F8FAFC",
    "--a-border":       "#E2E8F0",
    "--a-border-soft":  "#EEF2F7",
    "--a-ink":          "#0F172A",
    "--a-ink-muted":    "#475569",
    "--a-ink-faint":    "#7C8CA0",
    "--a-primary":      "#0D9488",
    "--a-primary-2":    "#14B8A6",
    "--a-on-primary":   "#FFFFFF",
    "--a-sidebar":      "#0B1424",
    "--a-sidebar-ink":  "#E6EDF7",
    "--a-sidebar-sub":  "#8FA3BF",
    "--a-grad-1":       "#0F172A",
    "--a-grad-2":       "#155E75",
    "--a-grad-acc":     "#0EA5E9",
    "--a-shadow":       "0 6px 18px rgba(15, 23, 42, .07)",
    "--a-shadow-lg":    "0 16px 40px rgba(15, 23, 42, .14)",
    "--a-ring":         "#0D9488",
    # severity (soft fg / soft bg / dot)
    "--a-crit-fg":  "#B91C1C", "--a-crit-bg": "#FEE2E2", "--a-crit-dot": "#DC2626",
    "--a-urg-fg":   "#B45309", "--a-urg-bg":  "#FEF3C7", "--a-urg-dot":  "#D97706",
    "--a-mon-fg":   "#15803D", "--a-mon-bg":  "#DCFCE7", "--a-mon-dot":  "#16A34A",
    "--a-data-fg":  "#334155", "--a-data-bg": "#E2E8F0", "--a-data-dot": "#64748B",
    "--a-warn-fg":  "#92400E", "--a-warn-bg": "#FEF3C7", "--a-warn-edge": "#F59E0B",
    "--a-info-fg":  "#1E3A8A", "--a-info-bg": "#EFF6FF", "--a-info-edge": "#93C5FD",
    "--a-neg-soft": "#FEE2E2", "--a-pos-soft": "#DCFCE7",
}

# --------------------------------------------------------------------------- #
# Dark theme
# --------------------------------------------------------------------------- #
DARK = {
    "--a-bg":           "#0A0F1E",
    "--a-bg-acc":       "#0D1629",
    "--a-surface":      "#111A2E",
    "--a-surface-2":    "#0E1626",
    "--a-border":       "#223049",
    "--a-border-soft":  "#1B2740",
    "--a-ink":          "#E7EDF7",
    "--a-ink-muted":    "#9FB0C9",
    "--a-ink-faint":      "#6B7C96",
    "--a-primary":      "#2DD4BF",
    "--a-primary-2":    "#5EEAD4",
    "--a-on-primary":   "#062A26",
    "--a-sidebar":       "#070C18",
    "--a-sidebar-ink":  "#E7EDF7",
    "--a-sidebar-sub":  "#7E8FAB",
    "--a-grad-1":       "#0C1226",
    "--a-grad-2":       "#134E5E",
    "--a-grad-3":       "#0E7490",
    "--a-shadow":       "0 8px 24px rgba(0, 0, 0, .35)",
    "--a-shadow-lg":    "0 20px 50px rgba(0, 0, 0, .5)",
    "--a-ring":          "#2DD4BF",
    "--a-crit-fg":  "#FCA5A5", "--a-crit-bg": "#3B1418", "--a-crit-dot": "#F87171",
    "--a-urg-fg":   "#FCD34D", "--a-urg-bg":  "#3A2A0C", "--a-urg-dot":  "#F59E0B",
    "--a-mon-fg":   "#86EFAC", "--a-mon-bg":  "#0F2E1C", "--a-mon-dot":  "#4ADE80",
    "--a-data-fg":  "#94A3B8", "--a-data-bg": "#1E293B", "--a-data-dot": "#94A3B8",
    "--a-warn-fg":  "#FDE68A", "--a-warn-bg": "#3A2A0C", "--a-warn-edge": "#B45309",
    "--a-info-fg":  "#93C5FD", "--a-info-bg": "#16233D", "--a-info-edge": "#3B82F6",
    "--a-neg-soft": "#3B1418", "--a-pos-soft": "#0F2E1C",
}

THEMES = {"light": LIGHT, "dark": DARK}


def severity_style(value, theme="light"):
    """value -> dict(fg, bg, dot) CSS var names for the given theme."""
    names = SEVERITY.get(str(value))
    if names is None:
        names = ("--a-data-fg", "--a-data-bg", "--a-data-dot")
    return {"fg": names[0], "bg": names[1], "dot": names[2]}


def css_vars(theme="light"):
    """Render the <:root> block declaring every semantic token for the theme."""
    t = THEMES[theme]
    return ":root {\n" + "".join(f"  {k}: {v};\n" for k, v in t.items()) + "}"


# --------------------------------------------------------------------------- #
# Brand mark
# --------------------------------------------------------------------------- #
LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34" viewBox="0 0 40 40">
  <rect width="40" height="40" rx="9" fill="#0F172A"/>
  <path d="M20 30v-14" stroke="#E2E8F0" stroke-width="3" stroke-linecap="round"/>
  <path d="M12 12h16M12 12 l8 -6 8 6" fill="none" stroke="#14B8A6" stroke-width="3"
        stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="20" cy="30" r="4.2" fill="none" stroke="#2DD4BF" stroke-width="3"/>
</svg>"""

LOGO_FILE = APP_DIR / "anchor-logo.png"


def logo_mark(width=34):
    if LOGO_FILE.exists():
        try:
            b64 = base64.b64encode(LOGO_FILE.read_bytes()).decode("ascii")
            return (f'<img src="data:image/png;base64,{b64}" width="{width}" '
                    f'style="border-radius:9px;vertical-align:middle;box-shadow:'
                    f'0 4px 14px rgba(0,0,0,.25)" alt="Anchor"/>')
        except OSError:
            pass
    return LOGO_SVG


# --------------------------------------------------------------------------- #
# The big injected stylesheet.
# --------------------------------------------------------------------------- #
def inject_css(theme="light"):
    return css_vars(theme) + f"""<style>
    /* ---------- base ---------- */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap');
    .anchor-root {{ color-scheme: {theme}; }}
    html, body, .stApp {{
      background: var(--a-bg); color: var(--a-ink);
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
    }}
    .stApp {{ padding-top: 0; overflow: clip; background:
       radial-gradient(1200px 600px at 85% -10%, var(--a-bg-acc), transparent 60%),
       var(--a-bg); }}
    footer, header[data-testid="stHeader"], div[data-testid="stToolbar"],
    #MainMenu, .stToolbar {{ display: none; }}
    section[data-testid="stMainBlockContainer"] {{ max-width: 1400px; padding-top: .4rem; }}
    ::selection {{ background: var(--a-primary); color: var(--a-on-primary); }}
    a {{ color: var(--a-primary); }}

    /* scrollbars */
    *::-webkit-scrollbar {{ width: 10px; height: 10px; }}
    *::-webkit-scrollbar-thumb {{ background: var(--a-border); border-radius: 8px; border: 2px solid var(--a-bg); }}
    *::-webkit-scrollbar-track {{ background: transparent; }}

    /* ---------- sidebar ---------- */
    section[data-testid="stSidebar"] {{
      background: linear-gradient(180deg, var(--a-sidebar), #0A101F);
      border-right: 1px solid var(--a-border);
    }}
    section[data-testid="stSidebar"] * {{ color: var(--a-sidebar-ink); }}
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{ color: var(--a-sidebar-sub); }}
    section[data-testid="stSidebar"] hr {{ border-color: var(--a-border); }}
    section[data-testid="stSidebar"] label [data-testid="stWidgetLabel"] p {{ color: var(--a-sidebar-sub); }}
    .anchor-brand {{ display:flex; align-items:center; gap:.6rem; padding:.15rem 0 .8rem 0; }}
    .anchor-brand .brand-name {{ font-size:1.3rem; font-weight:800; letter-spacing:-.02em; color:#fff; }}
    .anchor-brand .brand-sub {{ font-size:.7rem; color:var(--a-sidebar-sub); margin-top:-2px; }}

    /* sidebar nav radio - custom chips */
    div[data-testid="stSidebarNav"], section[data-testid="stSidebar"] label {{ color: var(--a-sidebar-sub) !important; }}
    section[data-testid="stSidebar"] [role="radiogroup"] {{ gap:.2rem; }}
    section[data-testid="stSidebar"] [role="radio"] {{
      margin:-2px; padding:.55rem .7rem; border-radius:8px; transition: all .15s ease;
    }}
    section[data-testid="stSidebar"] [role="radio"]:hover {{ background: rgba(255,255,255,.06); }}
    section[data-testid="stSidebar"] [role="radio"][aria-checked="true"] {{
      background: linear-gradient(90deg, rgba(45,212,191,.25), rgba(45,212,191,.06));
      color:#fff; font-weight:600; border-left:2px solid var(--a-primary);
    }}
    .sidebar-theme {{ margin-top:.3rem; padding:.3rem 0; }}
    .sidebar-theme label {{ color: var(--a-sidebar-sub); font-size:.78rem; font-weight:600; letter-spacing:.02em; }}

    /* ---------- topbar / header ---------- */
    .anchor-topbar {{
      display:flex; align-items:center; gap:1rem; justify-content:space-between;
      padding:.6rem 1.1rem; margin-bottom:1rem; border-radius:14px;
      background: var(--a-surface); border:1px solid var(--a-border);
      box-shadow: var(--a-shadow);
      backdrop-filter: blur(10px);
    }}
    .anchor-topbar .fresh-badge {{
      display:inline-flex; align-items:center; gap:.45rem; font-size:.8rem; font-weight:600;
      color: var(--a-ink-muted); padding:.32rem .7rem; border:1px solid var(--a-border);
      border-radius:999px; background: var(--a-surface-2); white-space:nowrap;
    }}
    .fresh-badge::before {{ content:''; width:8px; height:8px; border-radius:50%; }}
    .fresh-badge.teal-dot::before {{ background: var(--a-p-primary, var(--a-primary)); box-shadow:0 0 0 3px color-mix(in srgb, var(--a-primary) 25%, transparent); }}
    .fresh-badge.warn-dot::before {{ background: var(--a-warn-edge); box-shadow:0 0 0 3px color-mix(in srgb, var(--a-warn-edge) 25%, transparent); }}

    /* ---------- headings ---------- */
    .page-head {{ display:flex; align-items:flex-end; justify-content:space-between; gap:1rem; margin:.2rem 0 1rem; }}
    .page-head h1 {{ font-size:1.7rem; font-weight:800; letter-spacing:-.03em; margin:0; color: var(--a-ink); }}
    .page-head .head-sub {{ color: var(--a-ink-muted); font-size:.88rem; margin-top:.1rem; }}

    .section-title {{
      font-size:1.02rem; font-weight:700; color: var(--a-ink); margin:1.4rem 0 .45rem;
      display:flex; align-items:center; gap:.5rem;
    }}
    .section-title::before {{ content:''; width:4px; height:18px; border-radius:4px;
      background: linear-gradient(180deg, var(--a-primary), var(--a-primary-2)); }}
    .legend {{ color:var(--a-ink-faint); font-size:.78rem; margin:.1rem 0 .7rem; }}
    .muted {{ color: var(--a-ink-muted); font-size:.84rem; }}

    /* ---------- cards / KPIs ---------- */
    .card {{
      background: var(--a-surface); border:1px solid var(--a-border); border-radius:14px;
      padding:.95rem 1.05rem; box-shadow: var(--a-shadow);
      transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease;
    }}
    .kpi {{ display:flex; flex-direction:column; gap:.15rem; position:relative; overflow:hidden; }}
    .kpi::after {{ content:''; position:absolute; inset-inline:0; top:0; height:3px;
      background: linear-gradient(90deg, var(--a-border-soft), var(--a-border)); }}
    .kpi.crit::after {{ background: linear-gradient(90deg, var(--a-crit-dot), transparent); }}
    .kpi.urg::after  {{ background: linear-gradient(90deg, var(--a-urg-dot), transparent); }}
    .kpi.mon::after  {{ background: linear-gradient(90deg, var(--a-mon-dot), transparent); }}
    .kpi.dr::after   {{ background: linear-gradient(90deg, var(--a-data-dot), transparent); }}
    .kpi:hover, .card:hover {{ transform: translateY(-2px); box-shadow: var(--a-shadow-lg); border-color: var(--a-border); }}
    .kpi .kpi-value {{ font-size:2rem; font-weight:800; line-height:1; letter-spacing:-.02em;
      font-family:'JetBrains Mono', monospace; }}
    .kpi.crit .kpi-value {{ color: var(--a-crit-dot); }}
    .kpi.urg  .kpi-value {{ color: var(--a-urg-dot); }}
    .kpi.mon  .kpi-value {{ color: var(--a-mon-dot); }}
    .kpi.dr   .kpi-value {{ color: var(--a-data-fg); }}
    .kpi .kpi-label {{ font-size:.72rem; color: var(--a-ink-muted); font-weight:600; letter-spacing:.01em; }}

    /* clickable chip filter */
    .sevchips {{ display:flex; gap:.4rem; flex-wrap:wrap; margin:.2rem 0 .9rem; }}
    .sevchip {{
      display:inline-flex; align-items:center; gap:.45rem; cursor:pointer; user-select:none;
      font-size:.82rem; font-weight:600; padding:.42rem .8rem; border-radius:999px;
      border:1px solid var(--a-border); background: var(--a-surface); color: var(--a-ink-muted);
      transition: all .14s ease; box-shadow: var(--a-shadow);
    }}
    .sevchip:hover {{ transform: translateY(-1px); border-color: var(--a-border-acc, var(--a-primary)); }}
    .sevchip .n {{ font-family:'JetBrains Mono',monospace; font-weight:700; }}
    .sevchip[data-on="1"] {{ border-color: var(--chip-edge); background: var(--chip-bg); color: var(--chip-fg); }}
    .sevchip .dot {{ width:9px; height:9px; border-radius:50%; background: var(--chip-dot); }}

    /* ---------- severity / confidence pill ---------- */
    .pill {{ display:inline-flex; align-items:center; gap:.4rem;
      font-size:.72rem; font-weight:600; padding:.18rem .65rem; border-radius:999px;
      letter-spacing:.01em; }}
    .pill .dot {{ width:7px; height:7px; border-radius:50%; }}
    .pill.sev-crit {{ color: var(--a-crit-fg); background: var(--a-crit-bg); }}
    .pill.sev-crit .dot {{ background: var(--a-crit-dot); }}
    .pill.sev-urg  {{ color: var(--a-urg-fg);  background: var(--a-urg-bg); }}
    .pill.sev-urg  .dot {{ background: var(--a-urg-dot); }}
    .pill.sev-mon  {{ color: var(--a-mon-fg);  background: var(--a-mon-bg); }}
    .pill.sev-mon  .dot {{ background: var(--a-mon-dot); }}
    .pill.sev-data {{ color: var(--a-data-fg); background: var(--a-data-bg); }}
    .pill.sev-data .dot {{ background: var(--a-data-dot); }}
    .pill.sev-plain {{ color: var(--a-ink-muted); background: var(--a-surface-2); border:1px solid var(--a-border); }}
    .conf {{ color: var(--a-ink-muted); background: var(--a-surface-2); border:1px solid var(--a-border); }}
    .conf.high {{ color: var(--a-mon-fg); background: var(--a-pos-soft); }}
    .conf.med  {{ color: var(--a-urg-fg);  background: var(--a-urg-bg); }}
    .conf.low  {{ color: var(--a-data-fg); background: var(--a-data-bg); }}

    /* ---------- banners ---------- */
    .warn-banner, .restore-banner, .info-banner {{
      display:flex; gap:.7rem; align-items:flex-start; border-radius:12px;
      padding:.75rem 1rem; margin-bottom:1rem; font-size:.86rem; line-height:1.45;
      border:1px solid; box-shadow: var(--a-shadow);
    }}
    .warn-banner {{ background: var(--a-warn-bg); border-color: var(--a-warn-edge); color: var(--a-warn-fg); }}
    .restore-banner {{ background: var(--a-info-bg); border-color: var(--a-info-edge); color: var(--a-info-fg); }}
    .info-banner {{ background: var(--a-surface-2); border-color: var(--a-border); color: var(--a-info-fg, var(--a-ink-muted)); }}

    /* ---------- widgets: dataframe / tabs / buttons ---------- */
    div[data-testid="stDataFrame"] {{ border:1px solid var(--a-border); border-radius:12px; overflow:hidden; box-shadow: var(--a-shadow); }}
    div[data-testid="stDataFrame"] [data-testid="stCustomComponentV1"] {{ background: var(--a-surface); }}
    .stTabs [data-baseweb="tab-list"] {{ gap:.3rem; background: var(--a-surface-2); padding:.3rem; border-radius:12px; border:1px solid var(--a-border); }}
    .stTabs [data-baseweb="tab"] {{
      border-radius:8px; padding:.4rem .9rem; color: var(--a-ink-muted); font-weight:600;
    }}
    .stTabs [aria-selected="true"] {{ background: var(--a-surface); color: var(--a-ink); box-shadow: var(--a-shadow); }}
    .stButton > button, div[data-testid="stDownloadButton"] button {{
      border-radius:9px; font-weight:600; transition: all .15s ease;
    }}
    .stButton > button:hover, div[data-testid="stDownloadButton"] button:hover {{ transform: translateY(-1px); }}
    .stButton > button[kind="primary"] {{ background: linear-gradient(135deg, var(--a-primary), var(--a-primary-2));
      color: var(--a-on-primary); border:none; box-shadow: 0 6px 18px color-mix(in srgb, var(--a-primary) 35%, transparent); }}
    .stButton > button[kind="primary"]:hover {{ box-shadow: 0 10px 26px color-mix(in srgb, var(--a-primary) 45%, transparent); }}
    .stTextInput input, .stSelectbox > div > div, [data-testid="stFileUploaderDropzone"] {{
      border-radius:9px !important; }}
    [data-testid="stFileUploaderDropzone"] {{ border:1px dashed var(--a-border); background: var(--a-surface-2); }}
    div[data-baseweb="select"] > div {{ background: var(--a-surface); }}
    .stTextArea textarea {{ background: var(--a-surface); color: var(--a-ink); border-radius:10px; border:1px solid var(--a-border); }}

    /* tables */
    .stDataFrame, table {{
      color: var(--a-ink); --highlight-color: var(--a-surface-2);
    }}

    /* horizontal bar rows */
    .hbar-row {{ display:grid; grid-template-columns: minmax(0,1fr) auto; gap:.6rem 1rem;
      align-items:center; margin-bottom:.55rem; }}
    .hbar-label {{ font-size:.84rem; color: var(--a-ink-muted); font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .hbar-val {{ font-family:'JetBrains Mono',monospace; font-size:.84rem; font-weight:700; color: var(--a-ink); min-width:2.5rem; text-align:right; }}
    .hbar-track-wrap {{ grid-column: 1 / -1; }}
    .hbar-track-fill {{ height:8px; border-radius:999px; width:var(--w); background: linear-gradient(90deg, var(--c1), var(--c2)); box-shadow: 0 2px 6px color-mix(in srgb, var(--c1) 30%, transparent); transition: width .4s ease; }}

    /* mini stat chip */
    .chip-stat {{ display:inline-flex; flex-direction:column; padding:.55rem .8rem; border-radius:12px;
      background: var(--a-surface); border:1px solid var(--a-border); }}
    .chip-stat b {{ font-family:'JetBrains Mono',monospace; font-weight:800; color: var(--a-ink); }}
    .chip-stat span {{ font-size:.68rem; color: var(--a-ink-faint); font-weight:600; }}

    @media (prefers-reduced-motion: reduce) {{
      *, *::before, *::after {{ transition: none !important; animation: none !important; }}
    }}
    </style>"""