from django import forms
from django.contrib.auth import get_user_model

from apps.clients.forms import _style
from apps.members.models import WorkspaceMembership
from apps.projects.models import Project, ProjectKind, ProjectTemplate, Task


def staff_choices(workspace):
    """People who can own or do work for this client: its non-client members."""
    users = (
        get_user_model()
        .objects.filter(workspace_memberships__workspace=workspace)
        .exclude(
            workspace_memberships__workspace=workspace,
            workspace_memberships__workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT,
        )
        .distinct()
        .order_by("name", "email")
    )
    return users


def _date(**attrs):
    return forms.DateInput(attrs={"type": "date", **attrs})


class NewProjectForm(forms.Form):
    template = forms.ModelChoiceField(queryset=ProjectTemplate.objects.none(), label="Start from")
    name = forms.CharField(max_length=160)
    starts_on = forms.DateField(widget=_date(), label="Starts on")
    owner = forms.ModelChoiceField(queryset=get_user_model().objects.none(), required=False, label="Owner")

    def __init__(self, *args, workspace=None, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["template"].queryset = ProjectTemplate.objects.filter(organization=organization)
        self.fields["owner"].queryset = staff_choices(workspace)
        self.fields["owner"].label_from_instance = lambda u: u.name or u.email
        _style(self)


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = [
            "name",
            "kind",
            "status",
            "owner",
            "starts_on",
            "due_on",
            "description",
            "client_summary",
            "show_in_portal",
        ]
        widgets = {"starts_on": _date(), "due_on": _date()}
        labels = {
            "description": "Internal brief (never shown to the client)",
            "client_summary": "Summary for the client portal",
            "show_in_portal": "Show milestones and progress in the client portal",
        }

    def __init__(self, *args, workspace=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = staff_choices(workspace)
        self.fields["owner"].label_from_instance = lambda u: u.name or u.email
        _style(self)


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title", "status", "assignee", "starts_on", "due_on", "is_milestone", "depends_on", "description"]
        widgets = {"starts_on": _date(), "due_on": _date(), "depends_on": forms.SelectMultiple(attrs={"size": 5})}
        labels = {
            "is_milestone": "Milestone (shown to the client)",
            "depends_on": "Waits on",
            "description": "Notes (internal)",
            "starts_on": "Starts",
            "due_on": "Due",
        }

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = staff_choices(project.workspace)
        self.fields["assignee"].label_from_instance = lambda u: u.name or u.email
        others = Task.objects.filter(project=project)
        if self.instance.pk:
            others = others.exclude(pk=self.instance.pk)
        self.fields["depends_on"].queryset = others
        self.fields["depends_on"].required = False
        _style(self)

    def clean(self):
        cleaned = super().clean()
        start, due = cleaned.get("starts_on"), cleaned.get("due_on")
        if start and due and due < start:
            self.add_error("due_on", "The due date is before the start date.")
        return cleaned


class TemplateForm(forms.ModelForm):
    tasks_json = forms.CharField(widget=forms.HiddenInput)

    class Meta:
        model = ProjectTemplate
        fields = ["name", "kind", "description"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = ProjectKind.choices
        _style(self)

    def clean_tasks_json(self):
        import json

        try:
            rows = json.loads(self.cleaned_data["tasks_json"])
        except ValueError as exc:
            raise forms.ValidationError("The task list couldn't be read.") from exc
        if not isinstance(rows, list) or not rows:
            raise forms.ValidationError("Add at least one task.")
        tasks, keys = [], set()
        for i, row in enumerate(rows):
            title = str(row.get("title", "")).strip()[:200]
            if not title:
                continue
            key = str(row.get("key") or f"t{i}")[:40]
            keys.add(key)
            tasks.append(
                {
                    "key": key,
                    "title": title,
                    "offset_days": max(0, min(365, int(row.get("offset_days") or 0))),
                    "duration_days": max(1, min(365, int(row.get("duration_days") or 1))),
                    "milestone": bool(row.get("milestone")),
                    "assign": "owner" if row.get("assign") == "owner" else "",
                    "checklist": [str(c).strip()[:300] for c in row.get("checklist", []) if str(c).strip()][:30],
                    "depends_on": [str(k) for k in row.get("depends_on", [])],
                    "description": str(row.get("description", ""))[:2000],
                }
            )
        for task in tasks:
            task["depends_on"] = [k for k in task["depends_on"] if k in keys and k != task["key"]]
        if not tasks:
            raise forms.ValidationError("Add at least one task.")
        return tasks
