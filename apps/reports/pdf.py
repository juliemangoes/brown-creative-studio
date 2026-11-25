"""A monthly report as PDF, in the studio's palette (fpdf2 core fonts; Latin-1 covers English and Spanish).

Built from ``apps.reports.presentation.present`` so it shows exactly what the
portal shows. Internal notes are never read here.
"""

from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext as _
from fpdf import FPDF

from apps.reports.presentation import present
from apps.shoots.callsheet import _t

INK = (28, 23, 20)
MUTED = (95, 87, 80)
ACCENT = (111, 74, 50)
RULE = (231, 228, 224)
SUNKEN = (240, 238, 235)


class _Doc(FPDF):
    def __init__(self, footer_text):
        super().__init__(format="A4")
        self.footer_text = footer_text
        self.set_auto_page_break(True, margin=20)
        self.set_margins(18, 16, 18)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", size=7.5)
        self.set_text_color(*MUTED)
        self.cell(0, 4, _t(f"{self.footer_text} · {self.page_no()}"), align="C")


def _heading(pdf, text):
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*INK)
    pdf.cell(0, 7, _t(text), new_x="LMARGIN", new_y="NEXT")
    pdf.set_draw_color(*RULE)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(2)


def _para(pdf, text, size=10, color=INK):
    pdf.set_font("Helvetica", size=size)
    pdf.set_text_color(*color)
    pdf.multi_cell(0, size * 0.5, _t(text), new_x="LMARGIN", new_y="NEXT")


def render_report(report) -> bytes:
    view = present(report)
    pdf = _Doc(f"{settings.SITE_NAME} · {report.title}")
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_text_color(*ACCENT)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 5, _t(settings.SITE_NAME), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)
    pdf.set_font("Times", size=24)
    pdf.multi_cell(0, 11, _t(report.title), new_x="LMARGIN", new_y="NEXT")
    _para(pdf, f"{view['period_label']} · " + _("compared with %(previous)s") % {"previous": view["previous_label"]},
          9, MUTED)  # fmt: skip
    _para(pdf, _("%(count)s posts published") % {"count": view["published_count"]}, 9, MUTED)

    for title, text in view["summary"]:
        if text.strip():
            _heading(pdf, title)
            _para(pdf, text)

    _heading(pdf, _("The month in figures"))
    _figure_table(pdf, view["headline"], width)

    for table in view["platform_tables"]:
        _heading(pdf, table["label"])
        _figure_table(pdf, table["rows"], width)

    if view["goals"]:
        _heading(pdf, _("Goals"))
        pdf.set_font("Helvetica", size=9)
        for g in view["goals"]:
            pdf.set_text_color(*INK)
            line = f"{g['name']}: {g['actual_display'] or '-'} / {g['target_display']} ({g['label']})"
            pdf.multi_cell(0, 5, _t(line), new_x="LMARGIN", new_y="NEXT")

    if view["top_posts"]:
        _heading(pdf, _("Top posts by %(metric)s") % {"metric": view["rank_label"].lower()})
        for p in view["top_posts"]:
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.set_text_color(*INK)
            pdf.multi_cell(0, 5, _t(f"{p['title']} · {p['platform_label']} · {p['published']}"), new_x="LMARGIN",
                           new_y="NEXT")  # fmt: skip
            pdf.set_font("Helvetica", size=8.5)
            pdf.set_text_color(*MUTED)
            parts = [f"{f['label']}: {f['value'] or f['missing']}" for f in p["figures"]]
            pdf.multi_cell(0, 4.5, _t(" · ".join(parts)), new_x="LMARGIN", new_y="NEXT")
            if p.get("live_url"):
                pdf.set_text_color(*ACCENT)
                pdf.cell(0, 4.5, _t(p["live_url"]), link=p["live_url"], new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1.5)

    if view["breakdowns"]:
        _heading(pdf, _("What worked, by %(metric)s") % {"metric": view["rank_label"].lower()})
        for b in view["breakdowns"]:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*INK)
            pdf.cell(0, 5, _t(b["label"]), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", size=8.5)
            pdf.set_text_color(*MUTED)
            for r in b["rows"]:
                small = " " + _("(few posts)") if r["small"] else ""
                pdf.cell(0, 4.5, _t(f"{r['group']}: {r['average']} · {r['posts']} " + _("posts") + small),
                         new_x="LMARGIN", new_y="NEXT")  # fmt: skip

    _heading(pdf, _("About these figures"))
    seen = set()
    for f in [*view["headline"], *[r for t in view["platform_tables"] for r in t["rows"]]]:
        if f["metric"] in seen:
            continue
        seen.add(f["metric"])
        _para(pdf, f"{f['label']}: {f['definition']}", 8, INK)
        details = []
        if f["sources"]:
            details.append(_("Source") + ": " + "; ".join(f["sources"]))
        if f["updated"]:
            details.append(_("Last updated") + ": " + f["updated"])
        if details:
            _para(pdf, " · ".join(details), 7.5, MUTED)
    _para(pdf, _("Figures as of %(when)s.") % {"when": view["computed"]}, 7.5, MUTED)
    return bytes(pdf.output())


def _figure_table(pdf, rows, width):
    cols = (width * 0.34, width * 0.2, width * 0.14, width * 0.32)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*MUTED)
    for w, head in zip(cols, (_("Metric"), _("This period"), _("Change"), _("Note")), strict=True):
        pdf.cell(w, 6, _t(head))
    pdf.ln(6)
    for r in rows:
        pdf.set_font("Helvetica", size=9)
        pdf.set_text_color(*INK)
        y = pdf.get_y()
        pdf.cell(cols[0], 6, _t(r["label"]))
        if r["available"]:
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(cols[1], 6, _t(r["value"]))
            pdf.set_font("Helvetica", size=9)
            pdf.cell(cols[2], 6, _t(r["change"] or "-"))
        else:
            pdf.set_text_color(*MUTED)
            pdf.cell(cols[1] + cols[2], 6, _t(r["missing"]))
        note = "; ".join(r["notes"][:2])
        pdf.set_font("Helvetica", size=7)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(cols[3], 3.4, _t(note), new_x="LMARGIN", new_y="NEXT")
        pdf.set_y(max(pdf.get_y(), y + 6))
        pdf.set_x(pdf.l_margin)
        pdf.set_draw_color(*RULE)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
