import json

from django import forms

from apps.brand.models import BrandAsset, BrandStage
from apps.brand.services import HEX_RE, lines, pairs, unlines, unpairs
from apps.clients.forms import _style
from apps.studio.forms import MultipleFileField

LIST_FIELDS = {
    "values": "Values (one per line)",
    "personality": "Personality traits (one per line)",
    "competitors": "Competitors (one per line)",
    "voice_do": "We say (one per line)",
    "voice_dont": "We don't say (one per line)",
    "key_messages": "Key messages (one per line)",
}
PAIR_FIELDS = {
    "audiences": ("Audiences (Name: description, one per line)", "description"),
    "fonts": ("Fonts (Name: where it's used, one per line)", "usage"),
}
TEXT_FIELDS = {
    "purpose": "Purpose",
    "positioning": "Positioning",
    "voice": "Voice and tone",
    "tagline": "Tagline",
    "elevator_pitch": "Elevator pitch",
    "content_likes": "Content they like",
    "content_avoid": "Content to avoid",
    "internal_notes": "Internal notes (never shown to the client)",
}
SECTIONS = [
    ("Strategy", ["purpose", "positioning", "values", "personality", "audiences", "competitors"]),
    ("Voice and messaging", ["voice", "voice_do", "voice_dont", "tagline", "key_messages", "elevator_pitch"]),
    ("Visual identity", ["fonts"]),
    ("Content", ["content_likes", "content_avoid", "internal_notes"]),
]


class BrandProfileForm(forms.Form):
    colors_json = forms.CharField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, profile=None, **kwargs):
        initial = {}
        if profile is not None:
            initial = {name: getattr(profile, name) for name in TEXT_FIELDS}
            initial.update({name: unlines(getattr(profile, name)) for name in LIST_FIELDS})
            initial.update({name: unpairs(getattr(profile, name), second) for name, (_, second) in PAIR_FIELDS.items()})
            initial["colors_json"] = json.dumps(profile.colors or [])
        super().__init__(*args, initial=initial, **kwargs)
        for name, label in TEXT_FIELDS.items():
            widget = forms.TextInput() if name == "tagline" else forms.Textarea(attrs={"rows": 3})
            self.fields[name] = forms.CharField(
                label=label, required=False, widget=widget, max_length=200 if name == "tagline" else 5000
            )
        for name, label in LIST_FIELDS.items():
            self.fields[name] = forms.CharField(label=label, required=False, widget=forms.Textarea(attrs={"rows": 4}))
        for name, (label, _) in PAIR_FIELDS.items():
            self.fields[name] = forms.CharField(label=label, required=False, widget=forms.Textarea(attrs={"rows": 4}))
        _style(self)

    def sections(self):
        return [(title, [self[name] for name in names]) for title, names in SECTIONS]

    def clean_colors_json(self):
        raw = self.cleaned_data.get("colors_json") or "[]"
        try:
            rows = json.loads(raw)
        except ValueError as exc:
            raise forms.ValidationError("The colours couldn't be read.") from exc
        colors = []
        for row in rows if isinstance(rows, list) else []:
            hex_value = str(row.get("hex", "")).strip()
            if not hex_value:
                continue
            if not hex_value.startswith("#"):
                hex_value = f"#{hex_value}"
            if not HEX_RE.match(hex_value):
                raise forms.ValidationError(f"{hex_value} isn't a colour like #6B4F3A.")
            colors.append(
                {
                    "name": str(row.get("name", "")).strip()[:60],
                    "hex": hex_value.upper(),
                    "role": str(row.get("role", "")).strip()[:80],
                }
            )
        return colors[:24]

    def apply(self, profile):
        data = self.cleaned_data
        for name in TEXT_FIELDS:
            setattr(profile, name, data[name])
        for name in LIST_FIELDS:
            setattr(profile, name, lines(data[name])[:40])
        for name, (_, second) in PAIR_FIELDS.items():
            setattr(profile, name, pairs(data[name], second)[:20])
        profile.colors = data["colors_json"]
        return profile


class StageForm(forms.ModelForm):
    class Meta:
        model = BrandStage
        fields = ["status", "client_summary", "internal_notes", "revision_limit"]
        labels = {
            "client_summary": "What the client is signing off (shown to them)",
            "internal_notes": "Internal notes",
            "revision_limit": "Revision rounds in the contract",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Signed off is only ever set by the client.
        self.fields["status"].choices = [
            c
            for c in BrandStage.Status.choices
            if c[0] != BrandStage.Status.SIGNED_OFF or (self.instance and self.instance.status == c[0])
        ]
        if self.instance and self.instance.key != "identity":
            del self.fields["revision_limit"]
        _style(self)


class AssetUploadForm(forms.Form):
    uploads = MultipleFileField(label="Files")
    kind = forms.ChoiceField(choices=BrandAsset.Kind.choices, initial=BrandAsset.Kind.LOGO)
    title = forms.CharField(
        max_length=160,
        required=False,
        help_text="Leave blank to use each file's name. Give logo formats the same title to group them.",
    )
    variant = forms.CharField(max_length=80, required=False, help_text="e.g. Primary, Mark, One colour")
    stage = forms.ModelChoiceField(queryset=BrandStage.objects.none(), required=False, label="Deliverable for stage")
    client_visible = forms.BooleanField(required=False, label="Show in the client's Brand Hub")

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stage"].queryset = BrandStage.objects.filter(workspace=workspace)
        _style(self)
        self.fields["uploads"].widget.attrs["class"] = "block text-14"
