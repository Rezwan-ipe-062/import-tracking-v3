"""Small shared UI helpers for Anchor (brand pills, KPI chips, data slicing)."""

import datetime

import pandas as pd
import streamlit as st

from pipeline import freshness_state
from theme import SEVERITY, PRIORITY_ORDER, LOGO_SVG, logo_mark


def theme():
    """The current theme key, resolved from session state (default light)."""
    return st.session_state.get("anchor_theme", "light")


# ------------------------------------------------------------------------ #
# Data helpers
# ------------------------------------------------------------------------ #

def to_pandas(headers, rows):
    if not headers or rows is None:
        return pd.DataFrame(columns=list(headers or ["(no data)"]))
    return pd.DataFrame([list(r) for r in rows], columns=list(headers))


def col_value(row, headers, name):
    if name not in headers:
        return ""
    return row[headers.index(name)]


def fmt_date(v):
    if v is None:
        return ""
    if isinstance(v, datetime.datetime):
        return f"{v:%d %b %Y}"
    if isinstance(v, datetime.date):
        return v.strftime("%d %b %Y")
    s = str(v).strip()
    return s if s else ""


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------ #
# Branding
# ------------------------------------------------------------------------ #

def brand_block():
    st.markdown(
        f'<div class="anchor-brand">{logo_mark()}'
        f'<div><div class="brand-name">Anchor</div>'
        f'<div class="brand-sub">Import visibility &amp; action prioritisation</div></div>'
        f'</div>', unsafe_allow_html=True)


def stale(meta) -> bool:
    return freshness_state(meta)[0] == "stale"


def topbar(meta):
    state, note = freshness_state(meta)
    cls = "teal-dot" if state == "current" else "warn-dot"
    st.markdown(
        f'<div class="anchor-topbar">'
        f'{logo_mark()}'
        f'<div style="flex:1;min-width:0">'
        f'<div class="brand-name">Anchor</div>'
        f'<div class="brand-sub">Import visibility &amp; action prioritisation</div>'
        f'</div>'
        f'<span class="fresh-badge {cls}">{note}</span>'
        f'</div>', unsafe_allow_html=True)
    if state == "stale":
        st.markdown(
            '<div class="warn-banner"><div><b>Source data may be out of date.</b> '
            'Refresh the four source files before using this dashboard for '
            'today&#8217;s action decisions.</div></div>', unsafe_allow_html=True)


# ------------------------------------------------------------------------ #
# Pills / KPI
# ------------------------------------------------------------------------ #

def severity_pill(value):
    v = str(value)
    cls = SEVERITY.get(v, ("sev-data", "sev-data", "dot-data"))
    return (f'<span class="pill {cls[0] if len(cls)==3 else "sev-plain"}">'
            f'<span class="dot"></span>{v}</span>')


def confidence_pill(value):
    """High / Medium / Low data-confidence pill (theme-aware)."""
    key = str(value or "").lower()
    cls = {"high": "high", "medium": "med", "low": "low"}.get(key, "low")
    return (f'<span class="pill conf {cls}"><span class="dot"></span>{str(value or "")}</span>')


def global_search(headers, rows, key_prefix="gs"):
    """Header search across PO, product/AGI and container. Returns a chosen PO id."""
    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    with c1:
        q = st.text_input("Global search", placeholder="PO, product / AGI, or container",
                          key=f"{key_prefix}_q").strip()
    matches = []
    if q:
        doc_idx = headers.index("Purchasing Document") if "Purchasing Document" in headers else None
        text_idx = headers.index("Short Text") if "Short Text" in headers else None
        mat_idx = headers.index("Material") if "Material" in headers else None
        cont_idx = headers.index("Container No.") if "Container No." in headers else None
        haystacks = []
        for r in rows:
            parts = []
            for idx in (doc_idx, text_idx, mat_idx, cont_idx):
                if idx is not None and idx < len(r):
                    parts.append(str(r[idx]))
            haystacks.append("|".join(parts))
        q_l = q.lower()
        pois = []
        for r, hay in zip(rows, haystacks):
            if q_l in hay.lower() and doc_idx is not None and doc_idx < len(r):
                pois.append(str(r[doc_idx]))
        matches = sorted({p for p in pois if p})
    choice = ""
    if matches:
        with c:
            chosen = st.selectbox("Search results", ["—"] + matches, key=f"{key_prefix}_pick")
            if chosen and chosen != "—":
                choice = chosen
        if st.button("Open PO journey", key=f"{key_prefix}_open"):
            st.session_state["po"] = choice
            st.session_state["anchor_view"] = "app"
            st.session_state["page"] = "PO Journey"
            st.rerun()
    elif q:
        with c:
            st.caption("No PO, product or container matched.")
    return choice


def export_csv(headers, rows, filename, meta, filter_desc, subject):
    """Build a controlled CSV export with Anchor metadata as a comment block.

    Writes to store.export_dir(); returns the absolute path. Never an
    uncontrolled 'export everything' - callers pass an explicit subject and the
    active filter description.
    """
    import io
    import csv
    import datetime as _dt
    from store import export_dir

    lines = [
        "# Anchor - controlled export",
        f"# Anchor version: {meta.get('version') or '-'}",
        f"# Subject: {subject}",
        f"# Export timestamp: {_dt.datetime.now().isoformat(' ')}",
        f"# Master refresh: {meta.get('refreshed_at') or '-'}",
        f"# Source freshness: {meta.get('source_freshness') or 'see Thresholds & Refresh page'}",
        f"# Active filter: {filter_desc or 'none (all rows in view)'}",
        f"# Pipeline / threshold version: {meta.get('version') or '-'}",
        "",
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for r in rows:
        writer.writerow(["" if v is None else v for v in list(r)])
    out = export_dir() / filename
    out.write_text("\n".join(lines + [buffer.getvalue()]), encoding="utf-8")
    return out


def severity_cell_html(value):
    return severity_pill(value)


def kpi_row(kpis):
    """Render up to 7 KPI cards. kind: plain | crit | urg | mon | dr."""
    n = min(len(kpis), 7)
    cols = st.columns(n)
    for i, (label, value, kind) in enumerate(kpis[:n]):
        cls = "kpi" if kind == "plain" else f"kpi {kind}"
        with cols[i]:
            st.markdown(
                f'<div class="card {cls}"><div class="kpi-value">{value}</div>'
                f'<div class="kpi-label">{label}</div></div>',
                unsafe_allow_html=True)


def severity_chips(counts, key="sev"):
    """Clickable severity filter chips (native segmented_control).

    Returns the chosen severity value, or None when 'All' is selected. ``counts``
    maps a severity label -> int. Order follows PRIORITY_ORDER first, then any
    extra keys (e.g. 'No BD record') that are present.
    """
    if not counts:
        return None
    ordered = [v for v in PRIORITY_ORDER + ["No BD record", "No EE evidence"] if v in counts]
    for k in counts:
        if k not in ordered:
            ordered.append(k)
    options = ["All"] + ordered
    label_of = {o: (o if o == "All" else f"{o} · {counts[o]}") for o in options}
    sel = st.segmented_control(
        "Severity", options=options, format_func=lambda o: label_of[o],
        selection_mode="single", key=key, default="All",
        help="Filter the queue to one severity / evidence gap.",
    )
    return None if sel in (None, "All", "") else sel


def hbar(items, key="hb"):
    """Render a horizontal bar per (label, value), scaled to the max value."""
    if not items:
        return
    mx = max(v for _, v in items) or 1
    html = []
    for lbl, v in items:
        w = max(4.0, 100 * v / mx)
        html.append(
            f'<div class="hbar-row"><div class="hbar-label">{lbl}</div>'
            f'<div class="hbar-val">{v}</div>'
            f'<div class="hbar-track-wrap"><div class="hbar-track-fill" style="width:{w:.1f}%"></div></div></div>')
    st.markdown("".join(html), unsafe_allow_html=True)


def priority_sort_key(value):
    v = str(value).strip()
    return PRIORITY_ORDER.index(v) if v in PRIORITY_ORDER else 99


def section(title, legend=None):
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)
    if legend:
        st.markdown(f'<div class="legend">{legend}</div>', unsafe_allow_html=True)


def empty_state(title, body):
    st.markdown(f'<div class="card empty-state">'
                f'<div class="empty-icon">◌</div>'
                f'<div style="font-weight:600;font-size:1.05rem">{title}</div>'
                f'<div class="muted" style="margin-top:.35rem">{body}</div></div>',
                unsafe_allow_html=True)


def info_note(text):
    st.markdown(f'<div class="muted">{text}</div>', unsafe_allow_html=True)


def td_row(v):
    """Minimal markdown table rendering fallback (rarely used)."""
    return f"`{v}`"