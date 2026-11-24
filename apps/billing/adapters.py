"""Replaceable providers for payments, e-signature and certified tax documents, each with a mock.

Settings pick the provider (see ``.env.example``):

    BILLING_PAYMENT_PROVIDER = "none" | "mock"        online card payments (bank transfer always works)
    BILLING_SIGNATURE_PROVIDER = "builtin" | "mock"   how contracts are signed
    BILLING_TAX_PROVIDER = "none" | "mock"            certified tax documents (e.g. Guatemala FEL)

A real provider is added by subclassing the base class and registering it in
``PROVIDERS``. Mock providers never move money and never issue a document that is
valid for tax; everything they produce is labelled as a test.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

# ── Payments ─────────────────────────────────────────────────────────────────


@dataclass
class Checkout:
    url: str
    reference: str


class PaymentProvider:
    key = "none"
    label = "Bank transfer"
    online = False  # True when clients can pay by card in the portal
    is_test = False

    def start_checkout(self, invoice, amount, *, return_url) -> Checkout:
        raise NotImplementedError("No online payment provider is set up; clients pay by bank transfer.")


class MockPaymentProvider(PaymentProvider):
    """A pretend card checkout inside the app. No money moves; the page says so."""

    key = "mock"
    label = "Test card payment"
    online = True
    is_test = True

    def start_checkout(self, invoice, amount, *, return_url) -> Checkout:
        reference = f"mock_{secrets.token_hex(8)}"
        url = reverse("billing_portal:mock_checkout", kwargs={"invoice_id": invoice.id})
        return Checkout(url=f"{url}?ref={reference}&amount={amount}", reference=reference)


# ── E-signature ──────────────────────────────────────────────────────────────


@dataclass
class SignatureResult:
    provider: str
    reference: str
    document_hash: str


class SignatureProvider:
    """Built in: the signer types their name and agrees; we keep who, when, from where and a hash of the text."""

    key = "builtin"
    label = "Typed signature in the portal"
    is_test = False

    def document_hash(self, contract) -> str:
        return hashlib.sha256(contract.body.encode("utf-8")).hexdigest()

    def sign(self, contract, *, name, user, ip, user_agent) -> SignatureResult:
        return SignatureResult(self.key, "", self.document_hash(contract))


class MockSignatureProvider(SignatureProvider):
    """Stands in for an external e-signature service (an envelope id is recorded)."""

    key = "mock"
    label = "Test e-signature service"
    is_test = True

    def sign(self, contract, *, name, user, ip, user_agent) -> SignatureResult:
        return SignatureResult(self.key, f"mock-envelope-{secrets.token_hex(6)}", self.document_hash(contract))


# ── Certified tax documents ──────────────────────────────────────────────────


@dataclass
class TaxIssue:
    official_number: str
    issued_at: object
    detail: str = ""


class TaxProvider:
    key = "none"
    label = "No certified tax documents"
    enabled = False
    is_test = False

    def issue(self, invoice) -> TaxIssue:
        raise NotImplementedError("No tax-document provider is set up.")


class MockTaxProvider(TaxProvider):
    key = "mock"
    label = "Test tax-document provider (not valid for tax)"
    enabled = True
    is_test = True

    def issue(self, invoice) -> TaxIssue:
        return TaxIssue(f"TEST-{invoice.number}-{secrets.token_hex(3).upper()}", timezone.now(), "Mock: not valid.")


PROVIDERS = {
    "payment": {"none": PaymentProvider, "mock": MockPaymentProvider},
    "signature": {"builtin": SignatureProvider, "mock": MockSignatureProvider},
    "tax": {"none": TaxProvider, "mock": MockTaxProvider},
}
_SETTINGS = {
    "payment": ("BILLING_PAYMENT_PROVIDER", "none"),
    "signature": ("BILLING_SIGNATURE_PROVIDER", "builtin"),
    "tax": ("BILLING_TAX_PROVIDER", "none"),
}


def provider(kind: str):
    setting, default = _SETTINGS[kind]
    key = (getattr(settings, setting, default) or default).strip().lower()
    try:
        return PROVIDERS[kind][key]()
    except KeyError as exc:
        raise ValueError(
            f"Unknown {kind} provider {key!r}; set {setting} to one of {sorted(PROVIDERS[kind])}."
        ) from exc
