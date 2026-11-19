"""Projects and tasks: templates, scoping (cross-client and freelancer), views, workload, delivery."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.clients.models import Retainer
from apps.projects.models import Project, ProjectTemplate, SavedFilter, Task
from apps.projects.services import create_from_template, ensure_builtin_templates, retainer_delivery, studio_scope
from apps.studio.tests.helpers import make_account, make_post, make_studio


@pytest.fixture
def studio(db):
    people = make_studio()
    templates = ensure_builtin_templates(people["org"])
    start = timezone.localdate()
    people["templates"] = templates
    people["r_project"] = create_from_template(
        people["restaurant"],
        templates["social_retainer"],
        name="November social",
        starts_on=start,
        owner=people["manager"],
        created_by=people["owner"],
    )
    people["s_project"] = create_from_template(
        people["skincare"],
        templates["campaign"],
        name="Serum launch",
        starts_on=start,
        owner=people["owner"],
        created_by=people["owner"],
    )
    # One task for the freelancer (contributor on the restaurant).
    people["freelance_task"] = people["r_project"].tasks.get(template_key="edit")
    people["freelance_task"].assignee = people["contributor"]
    people["freelance_task"].save()
    people["other_task"] = people["r_project"].tasks.get(template_key="captions")
    return people


def _ws(name, ws, **kw):
    return reverse(name, kwargs={"workspace_id": ws.id, **kw})


# ── Templates and creation ───────────────────────────────────────────────────


def test_builtin_templates_are_created_once(studio):
    assert set(studio["templates"]) == {"social_retainer", "shoot", "campaign", "brand", "strategy"}
    ensure_builtin_templates(studio["org"])
    assert ProjectTemplate.objects.filter(organization=studio["org"]).count() == 5


def test_project_from_template_has_dates_dependencies_checklists_and_owner(studio):
    project = studio["r_project"]
    start = timezone.localdate()
    assert project.tasks.count() == len(studio["templates"]["social_retainer"].tasks)
    plan = project.tasks.get(template_key="plan")
    assert plan.starts_on == start and plan.due_on == start + timedelta(days=2)
    assert plan.assignee == studio["manager"] and plan.checklist.count() == 4
    approval = project.tasks.get(template_key="plan_approval")
    assert approval.is_milestone and list(approval.depends_on.all()) == [plan]
    assert AuditEvent.objects.filter(verb="project.created", workspace=studio["restaurant"]).exists()


def test_brand_template_has_a_signoff_milestone_per_stage(studio):
    keys = [t["key"] for t in studio["templates"]["brand"].tasks if t["milestone"]]
    assert keys == [
        f"{s}_signoff" for s in ("discovery", "research", "strategy", "verbal", "visual", "identity", "guidelines")
    ]
    strategy = [t["key"] for t in studio["templates"]["strategy"].tasks if t["milestone"]]
    assert strategy == ["discovery_signoff", "research_signoff", "strategy_signoff", "verbal_signoff"]


def test_task_always_lives_in_its_projects_client(studio):
    task = Task(workspace=studio["skincare"], project=studio["r_project"], title="Sneaky")
    task.save()
    assert task.workspace == studio["restaurant"]


# ── Scope: who sees which tasks ──────────────────────────────────────────────


def test_freelancer_sees_only_assigned_tasks(studio):
    scope = studio_scope(studio["contributor"], studio["org"])
    assert list(scope.tasks(studio["contributor"])) == [studio["freelance_task"]]
    assert list(scope.projects(studio["contributor"])) == [studio["r_project"]]


def test_editor_sees_all_tasks_of_their_client_only(studio):
    scope = studio_scope(studio["editor"], studio["org"])
    workspaces = {t.workspace_id for t in scope.tasks(studio["editor"])}
    assert workspaces == {studio["restaurant"].id}


# ── Client pages ─────────────────────────────────────────────────────────────

CLIENT_PAGES = [
    ("projects:list", {}),
    ("projects:project", {"project": "r_project"}),
]


def _url(studio, name, extra, ws_key="restaurant"):
    kwargs = {"project_id": studio[extra["project"]].id} if "project" in extra else {}
    return _ws(name, studio[ws_key], **kwargs)


@pytest.mark.parametrize("view", ["list", "board", "timeline", "calendar"])
def test_project_views_render(client, studio, view):
    client.force_login(studio["manager"])
    response = client.get(
        _ws("projects:project", studio["restaurant"], project_id=studio["r_project"].id) + f"?view={view}"
    )
    assert response.status_code == 200
    assert "Draft the month" in response.content.decode() or view in ("timeline", "calendar")


@pytest.mark.parametrize("view", ["list", "board", "timeline", "calendar"])
def test_all_tasks_views_render(client, studio, view):
    client.force_login(studio["owner"])
    response = client.get(reverse("projects:all") + f"?view={view}&status=all")
    assert response.status_code == 200


def test_client_projects_page_shows_delivery(client, studio):
    Retainer.objects.create(
        workspace=studio["restaurant"],
        name="Social",
        monthly_fee=Decimal("1000"),
        starts_on=date(2026, 1, 1),
        posts_per_month=12,
    )
    client.force_login(studio["owner"])
    body = client.get(_ws("projects:list", studio["restaurant"])).content.decode()
    assert "November social" in body and "Retainer delivery" in body and "of 12 posts" in body


def test_client_users_cannot_open_projects(client, studio):
    client.force_login(studio["restaurant_client"])
    assert client.get(_ws("projects:list", studio["restaurant"])).status_code == 403
    assert (
        client.get(_ws("projects:project", studio["restaurant"], project_id=studio["r_project"].id)).status_code == 403
    )
    response = client.get(reverse("projects:all"))
    assert response.status_code == 302 and response["Location"] == reverse("client_portal:dashboard")


def test_cross_client_project_and_task_ids_are_404(client, studio):
    client.force_login(studio["owner"])  # owner of both: only the id mismatch stops it
    s_task = studio["s_project"].tasks.first()
    assert (
        client.get(_ws("projects:project", studio["restaurant"], project_id=studio["s_project"].id)).status_code == 404
    )
    assert client.get(_ws("projects:task", studio["restaurant"], task_id=s_task.id)).status_code == 404
    assert (
        client.post(
            _ws("projects:task_status", studio["restaurant"], task_id=s_task.id), {"status": "done"}
        ).status_code
        == 404
    )
    assert client.post(_ws("projects:task_delete", studio["restaurant"], task_id=s_task.id)).status_code == 404
    s_task.refresh_from_db()
    assert s_task.status == "todo"


def test_staff_not_on_a_client_are_forbidden(client, studio):
    client.force_login(studio["editor"])  # restaurant only
    s_task = studio["s_project"].tasks.first()
    assert client.get(_ws("projects:list", studio["skincare"])).status_code == 403
    assert client.get(_ws("projects:task", studio["skincare"], task_id=s_task.id)).status_code == 403
    assert (
        client.post(_ws("projects:task_status", studio["skincare"], task_id=s_task.id), {"status": "done"}).status_code
        == 403
    )


def test_freelancer_cannot_see_or_change_unassigned_tasks(client, studio):
    client.force_login(studio["contributor"])
    other = studio["other_task"]
    assert client.get(_ws("projects:task", studio["restaurant"], task_id=other.id)).status_code == 404
    assert (
        client.post(_ws("projects:task_status", studio["restaurant"], task_id=other.id), {"status": "done"}).status_code
        == 404
    )
    mine = studio["freelance_task"]
    assert client.get(_ws("projects:task", studio["restaurant"], task_id=mine.id)).status_code == 200
    response = client.post(
        _ws("projects:task_status", studio["restaurant"], task_id=mine.id),
        {"status": "in_progress"},
        HTTP_ACCEPT="application/json",
    )
    assert response.json()["status"] == "in_progress"
    # …but cannot create or delete tasks, or edit the project.
    assert client.post(_ws("projects:task_delete", studio["restaurant"], task_id=mine.id)).status_code == 403
    assert client.post(_ws("projects:create", studio["restaurant"]), {"name": "x"}).status_code == 403
    body = client.get(reverse("projects:all") + "?status=all").content.decode()
    assert "Edit photos and videos" in body and "Write captions" not in body


def test_viewer_sees_tasks_but_cannot_change_them(client, studio):
    client.force_login(studio["viewer"])
    task = studio["other_task"]
    assert client.get(_ws("projects:task", studio["restaurant"], task_id=task.id)).status_code == 200
    assert (
        client.post(_ws("projects:task_status", studio["restaurant"], task_id=task.id), {"status": "done"}).status_code
        == 403
    )


def test_manager_creates_project_task_and_updates_status(client, studio):
    client.force_login(studio["manager"])
    response = client.post(
        _ws("projects:create", studio["restaurant"]),
        {
            "template": studio["templates"]["shoot"].id,
            "name": "December shoot",
            "starts_on": "2026-12-01",
            "owner": studio["manager"].id,
        },
    )
    project = Project.objects.get(name="December shoot")
    assert response.status_code == 302 and project.workspace == studio["restaurant"] and project.tasks.count() == 8
    client.post(
        _ws("projects:task_create", studio["restaurant"], project_id=project.id),
        {"title": "Book the drone", "status": "todo"},
    )
    task = project.tasks.get(title="Book the drone")
    client.post(_ws("projects:task_status", studio["restaurant"], task_id=task.id), {"status": "done"})
    task.refresh_from_db()
    assert task.status == "done" and task.completed_at is not None


def test_checklist_comments_and_links(client, studio):
    client.force_login(studio["contributor"])
    task = studio["freelance_task"]
    client.post(_ws("projects:checklist_add", studio["restaurant"], task_id=task.id), {"text": "Colour grade"})
    item = task.checklist.get(text="Colour grade")
    client.post(_ws("projects:checklist_toggle", studio["restaurant"], task_id=task.id, item_id=item.id))
    item.refresh_from_db()
    assert item.is_done
    client.post(
        _ws("projects:comment_add", studio["restaurant"], task_id=task.id), {"body": "Selects are in the folder"}
    )
    client.post(
        _ws("projects:link_add", studio["restaurant"], task_id=task.id),
        {"url": "https://drive.example/selects", "label": "Selects"},
    )
    client.post(
        _ws("projects:link_add", studio["restaurant"], task_id=task.id), {"url": "javascript:alert(1)", "label": "x"}
    )
    assert task.comments.count() == 1 and list(task.links.values_list("label", flat=True)) == ["Selects"]


def test_next_parameter_cannot_redirect_off_site(client, studio):
    client.force_login(studio["manager"])
    task = studio["other_task"]
    response = client.post(
        _ws("projects:task_status", studio["restaurant"], task_id=task.id),
        {"status": "done", "next": "https://evil.example/"},
    )
    assert response.status_code == 302 and response["Location"].startswith("/projects/")


# ── Workload, saved filters, templates ───────────────────────────────────────


def test_workload_counts_by_person_and_week(client, studio):
    client.force_login(studio["owner"])
    response = client.get(reverse("projects:workload"))
    assert response.status_code == 200
    rows = {(r["person"].email if r["person"] else None): r for r in response.context["workload"]["rows"]}
    assert rows["manager@studio.test"]["total"] > 0


def test_workload_is_for_project_managers(client, studio):
    client.force_login(studio["contributor"])
    assert client.get(reverse("projects:workload")).status_code == 403


def test_saved_filters_are_personal(client, studio):
    client.force_login(studio["owner"])
    client.post(reverse("projects:save_filter"), {"name": "Overdue", "query": "due=overdue"})
    saved = SavedFilter.objects.get(user=studio["owner"])
    client.force_login(studio["manager"])
    assert client.post(reverse("projects:delete_filter", kwargs={"filter_id": saved.id})).status_code == 404
    assert SavedFilter.objects.filter(pk=saved.pk).exists()


def test_template_editing_needs_manage_templates(client, studio):
    template = studio["templates"]["shoot"]
    client.force_login(studio["manager"])
    assert client.get(reverse("projects:template_edit", kwargs={"template_id": template.id})).status_code == 403
    client.force_login(studio["owner"])
    import json

    tasks = [
        {
            "key": "a",
            "title": "Scout",
            "offset_days": 0,
            "duration_days": 2,
            "milestone": False,
            "assign": "owner",
            "checklist": ["Photos"],
            "depends_on": [],
        },
        {
            "key": "b",
            "title": "Shoot",
            "offset_days": 3,
            "duration_days": 1,
            "milestone": True,
            "assign": "",
            "checklist": [],
            "depends_on": ["a", "zzz"],
        },
    ]
    response = client.post(
        reverse("projects:template_edit", kwargs={"template_id": template.id}),
        {"name": "Quick shoot", "kind": "shoot", "description": "", "tasks_json": json.dumps(tasks)},
    )
    assert response.status_code == 302
    template.refresh_from_db()
    assert template.name == "Quick shoot" and template.tasks[1]["depends_on"] == ["a"]


def test_templates_of_another_studio_are_404(client, studio):
    from apps.organizations.models import Organization

    other = Organization.objects.create(name="Other")
    foreign = ensure_builtin_templates(other)["shoot"]
    client.force_login(studio["owner"])
    assert client.get(reverse("projects:template_edit", kwargs={"template_id": foreign.id})).status_code == 404


# ── Retainer delivery ────────────────────────────────────────────────────────


def test_retainer_delivery_counts_posts_once_across_platforms(studio):
    from apps.composer.models import PlatformPost

    ws = studio["restaurant"]
    Retainer.objects.create(
        workspace=ws, name="Social", monthly_fee=Decimal("1000"), starts_on=date(2026, 1, 1), posts_per_month=4
    )
    ig, fb = make_account(ws, "IG"), make_account(ws, "FB", platform="facebook")
    now = timezone.now()
    post = make_post(ws, ig, status="published", when=now - timedelta(minutes=5))
    PlatformPost.objects.create(post=post, social_account=fb, status="published", scheduled_at=post.scheduled_at)
    PlatformPost.objects.filter(post=post).update(published_at=now - timedelta(minutes=5))
    data = retainer_delivery(ws)
    current = data["months"][0]
    assert current["is_current"] and current["planned"] == 4 and current["published"] == 1 and current["percent"] == 25
