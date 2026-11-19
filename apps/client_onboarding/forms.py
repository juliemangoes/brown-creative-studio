from django import forms

from apps.clients.forms import _style
from apps.clients.models import Contact, ServiceLine


class StartForm(forms.Form):
    services = forms.MultipleChoiceField(
        choices=ServiceLine.Kind.choices, widget=forms.CheckboxSelectMultiple, label="Services this onboarding covers"
    )
    contacts = forms.ModelMultipleChoiceField(
        queryset=Contact.objects.none(), widget=forms.CheckboxSelectMultiple, required=False, label="Who to invite"
    )
    welcome_message = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False, label="Welcome message")
    reminder_days = forms.CharField(
        required=False,
        label="Send reminders after (days)",
        help_text="Comma-separated, counted from today, e.g. 2, 5, 9, 14.",
    )
    kickoff_url = forms.URLField(
        required=False,
        assume_scheme="https",
        label="Booking page for the kickoff (optional)",
        help_text="Leave blank to offer times from this page instead.",
    )

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contacts"].queryset = Contact.objects.for_workspace(workspace.id).exclude(email="")
        self.fields["contacts"].label_from_instance = lambda c: f"{c.name} · {c.email}"
        _style(self)

    def clean_reminder_days(self):
        return parse_days(self.cleaned_data["reminder_days"])


def parse_days(raw):
    days = []
    for part in (raw or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit() or not 1 <= int(part) <= 60:
            raise forms.ValidationError("Use whole days between 1 and 60, separated by commas.")
        days.append(int(part))
    return sorted(set(days))


class ReminderForm(forms.Form):
    reminder_days = forms.CharField(required=False, label="Reminders after (days from the start)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)

    def clean_reminder_days(self):
        return parse_days(self.cleaned_data["reminder_days"])


class DetailsForm(forms.Form):
    legal_name = forms.CharField(max_length=255, required=False, label="Legal business name")
    website = forms.URLField(max_length=200, required=False, assume_scheme="https")
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False, label="Billing address")
    city = forms.CharField(max_length=120, required=False)
    country = forms.CharField(
        max_length=2, required=False, label="Country code", help_text="Two letters, e.g. BZ for Belize."
    )
    tax_id = forms.CharField(max_length=60, required=False, label="Tax or registration number (if you have one)")
    billing_name = forms.CharField(max_length=255, required=False, label="Who receives invoices?")
    billing_email = forms.EmailField(required=False, label="Their email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)

    def clean_country(self):
        return self.cleaned_data["country"].strip().upper()
