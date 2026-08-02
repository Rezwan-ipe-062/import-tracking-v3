"""Small shared UI helpers for Anchor (brand pills, KPIs, data slicing)."""

import datetime

import pandas as pd
import streamlit as st

from pipeline import freshness_state
from theme import SEVERITY, PRIORITY_ORDER, LOGO_SVG


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
        f'<div class="anchor-brand">{LOGO_SVG}'
        f'<div><div class="brand-name">Anchor</div>'
        f'<div class="brand-sub">Import visibility &amp; action prioritisation</div></div>'
        f'</div>', unsafe_allow_html=True)


def stale(meta) -> bool:
    return freshness_state(meta)[0] == "stale"


def topbar(meta):
    col_brand, col_status = st.columns([1.4, 2.2], vertical_alignment="center")
    with col_brand:
        brand_block()
    with col_status:
        state, note = freshness_state(meta)
        cls = "teal-dot" if state == "current" else "warn-dot"
        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;gap:.5rem;justify-content:flex-end">'
            f'<span class="fresh-badge {cls}">{note}</span></div>',
            unsafe_allow_html=True)
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
    style = SEVERITY.get(v, {"fg": "#64748B", "bg": "#F1F5F9", "dot": "#64748B"})
    return (f'<span class="pill" style="color:{style["fg"]};'
            f'background:{style["bg"]}"><span class="dot" '
            f'style="background:{style["dot"]}"></span>{v}</span>')


def confidence_pill(value):
    """Small neutral/blue/slate pill for High / Medium / Low data confidence.

    Data-gap labels use the Data Review palette, never red (urgent/critical red is
    reserved for those severity classes only).
    """
    palette = {
        "High": {"fg": "#0F766E", "bg": "#CCFBF1", "dot": "#0D9488"},
        "Medium": {"fg": "#B45309", "bg": "#FEF3C7", "dot": "#D97706"},
        "Low": {"fg": "#475569", "bg": "#E2E8F0", "dot": "#64748B"},
    }
    v = str(value or "")
    p = palette.get(v, palette["Low"])
    return (f'<span class="pill" style="color:{p["fg"]};background:{p["bg"]}">'
            f'<span class="dot" style="background:{p["dot"]}"></span>{v}</span>')


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
    n = len(kpis)
    cols = st.columns(min(n, 7))
    for i, (label, value, kind) in enumerate(kpis):
        cls = "kpi"
        if kind in ("crit", "urg", "mon", "dr"):
            cls = f"kpi {kind}"
        with cols[i % len(cols)]:
            st.markdown(
                f'<div class="card {cls}" style="margin:.15rem 0">'
                f'<div class="kpi-value">{value}</div>'
                f'<div class="kpi-label">{label}</div></div>',
                unsafe_allow_html=True)


def priority_sort_key(value):
    v = str(value).strip()
    return PRIORITY_ORDER.index(v) if v in PRIORITY_ORDER else 99


def section(title, legend=None):
    st.markdown(f'<div class="section-title">{title}</div>', unsafe_allow_html=True)
    if legend:
        st.markdown(f'<div class="legend">{legend}</div>', unsafe_allow_html=True)


def empty_state(title, body):
    st.markdown(f'<div class="card" style="text-align:center;padding:2.2rem">'
                f'<div style="font-weight:600;font-size:1.05rem">{title}</div>'
                f'<div class="muted" style="margin-top:.35rem">{body}</div></div>',
                unsafe_allow_html=True)


def info_note(text):
    st.markdown(f'<div class="muted">{text}</div>', unsafe_allow_html=True)


def td_row(v):
    """Minimal markdown table rendering fallback (rarely used)."""
    return f"`{v}`"