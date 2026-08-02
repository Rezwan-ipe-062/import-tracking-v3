"""Anchor design tokens and smoke the app's visual shell.

Single source of truth for colour, severity semantics and the injected CSS.
Kept as plain Python so Streamlit can inline it without a separate build step.
"""

# ---------------------------------------------------------------------------
# Brand + severity tokens (WCAG AA-conscious pairs)
# ---------------------------------------------------------------------------
NAVY = "#0F172A"          # primary brand / primary buttons
NAVY_SIDEBAR = "#111C2F"  # slightly lifted navy for the sidebar surface
TEAL = "#0D9488"          # accent (small, never dominant)
TEAL_DARK = "#0F766E"
BG = "#F8FAFC"            # app background (light grey)
SURFACE = "#FFFFFF"
BORDER = "#E2E8F0"
TEXT = "#0F172A"
TEXT_MUTED = "#64748B"

# Severity semantics: (pill text colour, pill bg, inline dot)
SEVERITY = {
    "Critical":   {"fg": "#B91C1C", "bg": "#FEE2E2", "dot": "#B91C1C"},
    "Urgent":     {"fg": "#B45309", "bg": "#FEF3C7", "dot": "#B45309"},
    "Monitor":    {"fg": "#15803D", "bg": "#DCFCE7", "dot": "#15803D"},
    "Data Review":{"fg": "#334155", "bg": "#E2E8F0", "dot": "#334155"},
}

PRIORITY_ORDER = ["Critical", "Urgent", "Data Review", "Monitor"]


def severity_style(value):
    """"severity -> (fg, bg)"""  # pragma: no cover
    return SEVERITY.get(str(value), {"fg": TEXT_MUTED, "bg": "#F1F5F9"})


LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34" viewBox="0 0 40 40">
  <rect width="40" height="40" rx="8" fill="#0F172A"/>
  <path d="M20 30v-14" stroke="#E2E8F0" stroke-width="3" stroke-linecap="round"/>
  <path d="M12 12h16M12 12 l8 -6 8 6" fill="none" stroke="#0D9488" stroke-width="3"
        stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="20" cy="30" r="4.2" fill="none" stroke="#0D9488" stroke-width="3"/>
</svg>"""


def inject_css(SEVERITY=SEVERITY, BG=BG, SURFACE=SURFACE, BORDER=BORDER,
               NAVY=NAVY, NAVY_SIDEBAR=NAVY_SIDEBAR, TEAL=TEAL,
               TEAL_DARK=TEAL_DARK, TEXT_MUTED=TEXT_MUTED):
    """Return the full <style> block for the app shell."""
    return f"""
    <style>
    /* ---------- base ---------- */
    @import url('https://fonts.googleapis.com/css2?family=Fira+Sans:wght@400;500;600;700&display=swap');
    html, body, .stApp {{ background: {BG}; color: #0E172A; font-family: 'Fira Sans', system-ui, sans-serif; }}
    .stApp {{ padding-top: 0; }}
    footer, header[data-testid="stHeader"] {{ background: transparent; }}
    /* hide the top streamlit bar so our own compact header leads */
    div[data-testid="stDecoration"], #MainMenu, footer {{ display: none; }}
    header[data-testid="stHeader"] {{ z-index: 0; }}

    /* ---------- sidebar ---------- */
    section[data-testid="stSidebar"] {{
        background: {NAVY_SIDEBAR};
        border-right: 1px solid #1E293B;
        padding-top: .5rem;
    }}
    section[data-testid="stSidebar"] * {{ color: #E2E8F0; }}
    section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{ color: #CBD5E1; }}
    section[data-testid="stSidebar"] hr {{ border-color: #1E293B; }}
    .anchor-brand {{ display:flex; align-items:center; gap:.55rem; padding:.15rem 0 .7rem 0; }}
    .anchor-brand .brand-name {{ font-size:1.35rem; font-weight:700; letter-spacing:-.02em; color:#FFFFFF; }}
    .anchor-brand .brand-sub {{ font-size:.72rem; color:#94A3B8; margin-top:-2px; }}

    /* ---------- top action bar ---------- */
    .anchor-topbar {{
        display:flex; align-items:center; gap:1rem; justify-content:space-between;
        padding:.65rem 1.1rem; margin-bottom:1rem;
        background:{SURFACE}; border:1px solid {BORDER}; border-radius:12px;
        box-shadow: 0 1px 2px rgba(15,23,42,.04);
    }}
    .anchor-topbar .part {{ display:flex; align-items:center; gap:.9rem; min-width:0;}}
    .anchor-topbar .fresh-badge {{
        display:inline-flex; align-items:center; gap:.4rem; font-size:.8rem;
        color:{TEXT_MUTED}; padding:.28rem .6rem; border:1px solid {BORDER};
        border-radius:999px; background:{BG}; white-space:nowrap;
    }}
    .anchor-topbar .fresh-badge.teal-dot::before {{
        content:''; width:8px; height:8px; border-radius:50%; background:{TEAL};
    }}
    .anchor-topbar .fresh-badge.warn-dot::before {{
        content:''; width:8px; height:8px; border-radius:50%; background:#D97706;
    }}

    /* ---------- cards / kpis ---------- */
    .card {{
        background:{SURFACE}; border:1px solid {BORDER}; border-radius:12px;
        padding: .9rem 1rem;
    }}
    .kpi {{ display:flex; flex-direction:column; gap:.1rem; }}
    .kpi .kpi-value {{ font-size:1.9rem; font-weight:700; line-height:1; letter-spacing:-.02em; }}
    .kpi .kpi-label {{ font-size:.74rem; color:{TEXT_MUTED}; font-weight:500; }}
    .kpi.crit .kpi-value {{ color:#B91C1C; }}
    .kpi.urg  .kpi-value {{ color:#B45309; }}
    .kpi.mon  .kpi-value {{ color:#15803D; }}
    .kpi.dr   .kpi-value {{ color:#334155; }}

    /* ---------- severity pill ---------- */
    .pill {{
        display:inline-flex; align-items:center; gap:.4rem;
        font-size:.72rem; font-weight:600; padding:.16rem .6rem; border-radius:999px;
    }}
    .pill .dot {{ width:7px; height:7px; border-radius:50%; }}

    /* ---------- warning banner ---------- */
    .warn-banner {{
        display:flex; gap:.7rem; align-items:flex-start;
        background:#FEF3C7; border:1px solid #F59E0B; color:#78350F;
        border-radius:10px; padding:.7rem 1rem; margin-bottom:1rem;
        font-size:.86rem;
    }}
    .warn-banner b {{ color:#92400E; }}
    .restore-banner {{
        display:flex; gap:.7rem; align-items:flex-start;
        background:#EFF6FF; border:1px solid #93C5FD; color:#1E3A8A;
        border-radius:10px; padding:.7rem 1rem; margin-bottom:1rem; font-size:.86rem;
    }}

    /* ---------- sections / tables ---------- */
    .section-title {{ font-size:1.05rem; font-weight:700; color:#0E172A; margin:1.1rem 0 .5rem; }}
    .legend {{ display:flex; gap:1rem; flex-wrap:wrap; font-size:.78rem; color:{TEXT_MUTED}; margin-bottom:.6rem; }}
    .muted {{ color:{TEXT_MUTED}; font-size:.82rem; }}
    div[data-testid="stDataFrame"] {{ border:1px solid {BORDER}; border-radius:10px; overflow:hidden; }}
    .stButton > button {{ border-radius:9px; font-weight:600; }}
    .stButton > button[kind="primary"] {{ background:{NAVY}; color:#fff; border:1px solid {NAVY}; }}
    .stButton > button[kind="primary"]:hover {{ background:{NAVY}; }}
    h1,h2,h3 {{ letter-spacing:-.02em; color:#0E172A; }}
    .sidebar-header {{ padding: .4rem 0 .9rem 0; }}
    </style>
    """