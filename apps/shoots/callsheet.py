"""The call sheet as a PDF (fpdf2, core fonts: Latin-1 covers English and Spanish)."""

from __future__ import annotations

from fpdf import FPDF

INK = (43, 33, 27)
MUTED = (110, 96, 86)
ACCENT = (107, 79, 58)
RULE = (226, 216, 205)


def _t(value) -> str:
    """Core PDF fonts are Latin-1: replace anything outside it rather than fail."""
    return (
        str(value or "")
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
        .replace("—", "-")
        .encode("latin-1", "replace")
        .decode("latin-1")
    )


class _Sheet(FPDF):
    def __init__(self, studio):
        super().__init__(format="A4")
        self.studio = studio
        self.set_auto_page_break(True, margin=18)
        self.set_margins(18, 16, 18)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", size=8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, _t(f"{self.studio} · call sheet · page {self.page_no()}"), align="C")


def render_pdf(shoot, *, studio_name, contact_line="") -> bytes:
    pdf = _Sheet(studio_name)
    pdf.add_page()
    pdf.set_text_color(*ACCENT)
    pdf.set_font("Times", size=11)
    pdf.cell(0, 6, _t(studio_name), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)
    pdf.set_font("Times", size=24)
    pdf.cell(0, 12, _t(f"Call sheet: {shoot.title}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    pdf.set_text_color(*MUTED)
    wrap = f" - wrap {shoot.wrap_time:%H:%M}" if shoot.wrap_time else ""
    pdf.cell(
        0,
        7,
        _t(f"{shoot.workspace.name} · {shoot.date:%A %d %B %Y} · call {shoot.call_time:%H:%M}{wrap}"),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(3)

    def section(title):
        pdf.ln(2)
        pdf.set_draw_color(*RULE)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(2)
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", style="B", size=11)
        pdf.cell(0, 7, _t(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=10)

    def para(text):
        pdf.set_text_color(*INK)
        pdf.multi_cell(0, 5.5, _t(text), new_x="LMARGIN", new_y="NEXT")

    if shoot.locations:
        section("Where")
        for place in shoot.locations:
            para(f"- {place}")
    if contact_line or shoot.client_contact_id:
        section("Contacts")
        if contact_line:
            para(contact_line)
        if shoot.client_contact_id:
            c = shoot.client_contact
            para(
                f"On site for {shoot.workspace.name}: {c.name}"
                + (f", {c.phone}" if c.phone else "")
                + (f", {c.email}" if c.email else "")
            )
    if shoot.run_of_show:
        section("Run of show")
        for row in shoot.run_of_show:
            para(f"{row.get('time', '')}   {row.get('item', '')}")
    if shoot.talent:
        section("Talent")
        for person in shoot.talent:
            para(f"- {person.get('name', '')}" + (f" ({person.get('role')})" if person.get("role") else ""))
    for label, value in (
        ("Wardrobe", shoot.wardrobe),
        ("Props", shoot.props),
        ("Permits", shoot.permits),
        ("Release forms", shoot.release_forms),
        ("Notes", shoot.client_notes),
    ):
        if value:
            section(label)
            para(value)
    if shoot.prep_checklist:
        section("Before the shoot, please")
        for item in shoot.prep_checklist:
            para(f"[{'x' if item.get('done') else ' '}] {item.get('text', '')}")
    shots = list(shoot.shots.order_by("position", "code"))
    if shots:
        section(f"Shot list ({len(shots)})")
        for shot in shots:
            extra = " · ".join(x for x in (shot.get_priority_display(), shot.location) if x)
            para(f"{shot.code}  {shot.description}" + (f"  ({extra})" if extra else ""))
    return bytes(pdf.output())
