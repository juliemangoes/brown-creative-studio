from django import forms
from django.conf import settings

from apps.clients.models import ClientProfile, Contact, Retainer, ServiceLine


def _style(form):
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs.setdefault("class", "checkbox")
        elif isinstance(widget, forms.CheckboxSelectMultiple):
            continue
        elif isinstance(widget, forms.Select):
            widget.attrs.setdefault("class", "select")
        elif isinstance(widget, forms.Textarea):
            widget.attrs.setdefault("class", "textarea")
            widget.attrs.setdefault("rows", 3)
        else:
            widget.attrs.setdefault("class", "input")
    return form


def currency_choices():
    return [(c, c) for c in settings.BILLING_CURRENCIES]


class NewClientForm(forms.Form):
    name = forms.CharField(max_length=100, label="Client name")
    stage = forms.ChoiceField(
        choices=ClientProfile.Stage.choices, initial=ClientProfile.Stage.LEAD, label="Pipeline stage"
    )
    industry = forms.CharField(max_length=120, required=False)
    services = forms.MultipleChoiceField(
        choices=ServiceLine.Kind.choices, widget=forms.CheckboxSelectMultiple, required=False, label="Services"
    )
    contact_name = forms.CharField(max_length=255, required=False, label="Main contact")
    contact_email = forms.EmailField(required=False, label="Contact email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class ProfileForm(forms.ModelForm):
    website = forms.URLField(required=False, max_length=200, assume_scheme="https")

    class Meta:
        model = ClientProfile
        fields = [
            "stage",
            "legal_name",
            "industry",
            "website",
            "city",
            "country",
            "portal_language",
            "currency",
            "lead_source",
            "internal_notes",
        ]
        labels = {
            "portal_language": "Portal language",
            "country": "Country code",
            "internal_notes": "Internal notes (never shown to the client)",
        }
        help_texts = {"country": "Two letters, for example BZ for Belize or GT for Guatemala."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"] = forms.ChoiceField(choices=currency_choices())
        _style(self)

    def clean_country(self):
        return self.cleaned_data["country"].strip().upper()


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ["name", "title", "email", "phone", "is_primary", "is_approver", "is_billing", "notes"]
        labels = {"is_primary": "Main contact", "is_approver": "Approves content", "is_billing": "Receives invoices"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class ServiceForm(forms.ModelForm):
    class Meta:
        model = ServiceLine
        fields = ["kind", "status", "started_on", "ended_on", "notes"]
        widgets = {
            "started_on": forms.DateInput(attrs={"type": "date"}),
            "ended_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class RetainerForm(forms.ModelForm):
    class Meta:
        model = Retainer
        fields = [
            "name",
            "monthly_fee",
            "currency",
            "starts_on",
            "ends_on",
            "posts_per_month",
            "reels_per_month",
            "stories_per_month",
            "shoot_days_per_month",
            "revision_rounds",
            "scope_notes",
        ]
        widgets = {
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"] = forms.ChoiceField(choices=currency_choices())
        _style(self)
