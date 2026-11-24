"""Invoices and credit notes as PDF (fpdf2, core fonts: Latin-1 covers English and Spanish).

An app-made invoice is never presented as a certified tax document: unless an
authorised provider issued one (``TaxDocument``), the footer says so.
"""

from __future__ import annotations

from fpdf import FPDF

from apps.shoots.callsheet import _t

INK = (28, 23, 20)
MUTED = (95, 87, 80)
ACCENT = (111, 74, 50)
RULE = (231, 228, 224)


class _Doc(FPDF):
    def __init__(self, footer_text):
        super().__init__(format="A4")
        self.footer_text = footer_text
        self.set_auto_page_break(True, margin=22)
        self.set_margins(18, 16, 18)

    def footer(self):
        self.set_y(-18)
        self.set_font("Helvetica", size=7.5)
        self.set_text_color(*MUTED)
        self.multi_cell(0, 3.6, _t(self.footer_text), align="C", new_x="LMARGIN", new_y="NEXT")
        self.cell(0, 4, _t(f"Page {self.page_no()}"), align="C")


def _money(currency, value) -> str:
    return f"{currency} {value:,.2f}"


def render_invoice(invoice) -> bytes:
    from apps.billing.services import settings_for, tax_breakdown

    studio = settings_for(invoice.workspace.organization)
    tax_doc = getattr(invoice, "tax_document", None)
    if tax_doc and tax_doc.status == "issued" and not tax_doc.is_test:
        legal = f"Certified tax document {tax_doc.official_number}, issued by {tax_doc.provider}."
    else:
        legal = "This document is not a certified tax document."
    footer = " · ".join(x for x in (studio.footer_note, legal) if x)
    pdf = _Doc(footer)
    pdf.add_page()

    title = invoice.get_kind_display()
    if invoice.status == "draft":
        title = f"Draft {title.lower()}"
    elif invoice.status == "void":
        title = f"{title} (void)"

    # Studio and title.
    pdf.set_text_color(*ACCENT)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 6, _t(studio.legal_name or invoice.workspace.organization.name), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", size=9)
    for line in [*studio.address.splitlines(), studio.tax_id and f"Tax ID {studio.tax_id}", studio.email, studio.phone]:
        if line:
            pdf.cell(0, 4.5, _t(line), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 11, _t(f"{title} {invoice.number}".strip()), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Bill to / dates.
    top = pdf.get_y()
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(90, 5, "BILL TO", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", size=10)
    for line in [invoice.bill_to_name, *invoice.bill_to_address.splitlines(),
                 invoice.bill_to_tax_id and f"Tax ID {invoice.bill_to_tax_id}"]:  # fmt: skip
        if line:
            pdf.cell(90, 5, _t(line), new_x="LMARGIN", new_y="NEXT")
    bottom = pdf.get_y()
    pdf.set_xy(118, top)
    rows = [
        ("Issued", invoice.issue_date and invoice.issue_date.strftime("%d %b %Y")),
        ("Due", (not invoice.is_credit_note) and invoice.due_date and invoice.due_date.strftime("%d %b %Y")),
        ("Credits", invoice.credits.number if invoice.is_credit_note and invoice.credits_id else ""),
        ("Period", invoice.period_start and f"{invoice.period_start:%d %b} - {invoice.period_end:%d %b %Y}"),
        ("Currency", invoice.currency),
    ]
    for label, value in rows:
        if value:
            pdf.set_x(118)
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*MUTED)
            pdf.cell(26, 5, _t(label.upper()))
            pdf.set_font("Helvetica", size=10)
            pdf.set_text_color(*INK)
            pdf.cell(0, 5, _t(value), new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(max(bottom, pdf.get_y()) + 8)

    # Lines.
    widths = (92, 18, 30, 34)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*MUTED)
    pdf.set_draw_color(*RULE)
    for header, width, align in zip(("DESCRIPTION", "QTY", "UNIT PRICE", "AMOUNT"), widths, "LRRR", strict=True):
        pdf.cell(width, 7, header, border="B", align=align)
    pdf.ln()
    pdf.set_font("Helvetica", size=10)
    pdf.set_text_color(*INK)
    for line in invoice.lines.all():
        desc = line.description + (f" (-{line.discount_percent:g}%)" if line.discount_percent else "")
        y = pdf.get_y()
        pdf.multi_cell(widths[0], 6, _t(desc), border="B", new_x="RIGHT", new_y="TOP")
        h = max(pdf.get_y() - y, 6) if pdf.get_y() > y else 6
        pdf.set_xy(18 + widths[0], y)
        pdf.cell(widths[1], h, f"{line.quantity:g}", border="B", align="R")
        pdf.cell(widths[2], h, f"{line.unit_price:,.2f}", border="B", align="R")
        pdf.cell(widths[3], h, f"{line.net:,.2f}", border="B", align="R", new_x="LMARGIN", new_y="NEXT")

    # Totals.
    pdf.ln(3)
    totals = [("Subtotal", invoice.subtotal)]
    if invoice.discount_total:
        totals.append(("Discounts", -invoice.discount_total))
    for name, rate, amount in tax_breakdown(invoice):
        totals.append((f"{name} {rate:g}%", amount))
    totals.append(("Total", invoice.total))
    if not invoice.is_credit_note:
        if invoice.amount_credited:
            totals.append(("Credited", -invoice.amount_credited))
        if invoice.amount_paid:
            totals.append(("Paid", -invoice.amount_paid))
        totals.append(("Balance due", invoice.balance))
    for label, value in totals:
        bold = label in ("Total", "Balance due")
        pdf.set_x(118)
        pdf.set_font("Helvetica", "B" if bold else "", 11 if bold else 10)
        pdf.cell(38, 7, _t(label))
        pdf.cell(36, 7, _money(invoice.currency, value), align="R", new_x="LMARGIN", new_y="NEXT")
    if invoice.base_currency != invoice.currency:
        pdf.set_x(118)
        pdf.set_font("Helvetica", size=8)
        pdf.set_text_color(*MUTED)
        pdf.cell(74, 5, _t(f"Rate used: 1 {invoice.currency} = {invoice.exchange_rate:g} {invoice.base_currency}"),
                 align="R", new_x="LMARGIN", new_y="NEXT")  # fmt: skip
        pdf.set_text_color(*INK)

    # Notes and how to pay.
    if invoice.notes:
        pdf.ln(6)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 5, "NOTES", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=10)
        pdf.set_text_color(*INK)
        pdf.multi_cell(0, 5, _t(invoice.notes), new_x="LMARGIN", new_y="NEXT")
    if not invoice.is_credit_note and (studio.bank_details or studio.payment_instructions):
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 5, "HOW TO PAY", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=10)
        pdf.set_text_color(*INK)
        pdf.multi_cell(
            0, 5, _t("\n".join(x for x in (studio.bank_details, studio.payment_instructions) if x)),
            new_x="LMARGIN", new_y="NEXT",
        )  # fmt: skip
        pdf.multi_cell(
            0, 5, _t(f"Please quote {invoice.number} as the payment reference."), new_x="LMARGIN", new_y="NEXT"
        )
    return bytes(pdf.output())
