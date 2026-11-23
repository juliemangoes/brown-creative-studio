"""Studio side: a post's full history, overriding a client approval, portal links, the approval rule."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.composer.models import Post
from apps.members.decorators import require_permission
from apps.members.models import WorkspaceMembership
from apps.reviews.history import post_history
from apps.reviews.models import ApprovalRule
from apps.reviews.services import clearance, due_at, override_client_approval
from apps.studio.decorators import in_local_time
from apps.studio.permissions import has_studio_permission, workspace_permission


def _post(request, post_id):
    return get_object_or_404(Post.objects.filter(workspace=request.workspace), pk=post_id)


@require_permission("view_plan")
@in_local_time
def post_review(request, workspace_id, post_id):
    post = _post(request, post_id)
    platform_posts = list(post.platform_posts.select_related("social_account"))
    states = [(pp, clearance(pp), due_at(pp)) for pp in platform_posts]
    return render(
        request,
        "reviews/post.html",
        {
            "workspace": request.workspace,
            "post": post,
            "states": states,
            "history": post_history(
                post, include_internal=request.workspace_membership.effective_permissions.get("view_internal", False)
            ),
            "media": list(post.media_attachments.select_related("media_asset").order_by("position")),
            "can_override": has_studio_permission(request.org_membership, "override_approval")
            and any(pp.status == "pending_client" for pp in platform_posts),
            "mode": request.workspace.get_approval_workflow_mode_display(),
        },
    )


@require_permission("approve_posts")
@require_POST
def override(request, workspace_id, post_id):
    post = _post(request, post_id)
    try:
        moved = override_client_approval(post, request.user, request.POST.get("reason", ""))
    except PermissionError as exc:
        raise PermissionDenied(str(exc)) from exc
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Client approval overridden for {len(moved)} platform post{'s' if len(moved) != 1 else ''}. The reason is in the history and the audit log.",
        )
    return redirect("reviews:post", workspace_id=workspace_id, post_id=post.id)


def _require_manage(request):
    if not workspace_permission(request, "manage_client_profile"):
        raise PermissionDenied


@require_permission("view_client_profile")
@in_local_time
def portal_links(request, workspace_id):
    from apps.client_portal.models import MagicLinkToken
    from apps.clients.views import _context as client_context

    ctx = client_context(request, "contacts")
    now = timezone.now()
    clients = WorkspaceMembership.objects.filter(
        workspace=request.workspace, workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT
    ).select_related("user", "custom_role")
    tokens = MagicLinkToken.objects.filter(workspace=request.workspace, user__in=[m.user for m in clients]).order_by(
        "-created_at"
    )
    by_user: dict = {}
    for token in tokens:
        by_user.setdefault(token.user_id, []).append(token)
    ctx.update(
        {
            "rows": [(m, by_user.get(m.user_id, [])[:5]) for m in clients],
            "now": now,
            "can_manage": workspace_permission(request, "manage_client_profile"),
        }
    )
    return render(request, "reviews/portal_links.html", ctx)


@require_permission("view_client_profile")
@require_POST
def revoke_link(request, workspace_id, token_id):
    from apps.audit.services import record
    from apps.client_portal.models import MagicLinkToken
    from apps.client_portal.services import revoke_magic_link

    _require_manage(request)
    token = get_object_or_404(MagicLinkToken.objects.filter(workspace=request.workspace), pk=token_id)
    revoke_magic_link(token.id, request.workspace)
    record(
        "permission.portal_link_revoked",
        f"A portal sign-in link for {token.user.email} was revoked.",
        workspace=request.workspace,
        actor=request.user,
        data={"user": token.user.email},
    )
    messages.success(request, f"The link for {token.user.name or token.user.email} no longer works.")
    return redirect("reviews:portal_links", workspace_id=workspace_id)


@require_permission("view_client_profile")
@require_POST
def revoke_all(request, workspace_id, membership_id):
    """Revoke every open link for one person (e.g. a lost phone, someone leaving the client)."""
    from apps.audit.services import record
    from apps.client_portal.models import MagicLinkToken

    _require_manage(request)
    membership = get_object_or_404(
        WorkspaceMembership.objects.filter(
            workspace=request.workspace, workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT
        ),
        pk=membership_id,
    )
    count = MagicLinkToken.objects.filter(
        workspace=request.workspace, user=membership.user, expires_at__gt=timezone.now()
    ).update(expires_at=timezone.now())
    record(
        "permission.portal_link_revoked",
        f"All portal sign-in links for {membership.user.email} were revoked.",
        workspace=request.workspace,
        actor=request.user,
        data={"user": membership.user.email, "count": count},
    )
    messages.success(
        request, f"{count} link{'s' if count != 1 else ''} for {membership.user.name or membership.user.email} revoked."
    )
    return redirect("reviews:portal_links", workspace_id=workspace_id)


@require_permission("view_client_profile")
@require_http_methods(["GET", "POST"])
def approval_rule(request, workspace_id):
    from apps.audit.services import record
    from apps.clients.views import _context as client_context

    ws = request.workspace
    rule, _ = ApprovalRule.objects.get_or_create(workspace=ws)
    members = WorkspaceMembership.objects.filter(workspace=ws).select_related("user")
    clients = [m.user for m in members if m.workspace_role == WorkspaceMembership.WorkspaceRole.CLIENT]
    staff = [
        m.user
        for m in members
        if m.workspace_role != WorkspaceMembership.WorkspaceRole.CLIENT and m.effective_permissions.get("approve_posts")
    ]
    if request.method == "POST":
        _require_manage(request)
        rule.internal_hours = max(1, min(720, int(request.POST.get("internal_hours") or 24)))
        rule.client_hours = max(1, min(720, int(request.POST.get("client_hours") or 48)))
        rule.save()
        rule.internal_approvers.set([u for u in staff if str(u.pk) in request.POST.getlist("internal_approvers")])
        rule.client_approvers.set([u for u in clients if str(u.pk) in request.POST.getlist("client_approvers")])
        if (
            request.POST.get("mode") in ("required_internal", "required_internal_and_client")
            and request.POST["mode"] != ws.approval_workflow_mode
        ):
            ws.approval_workflow_mode = request.POST["mode"]
            ws.save(update_fields=["approval_workflow_mode"])
        record(
            "permission.approval_rule_changed",
            f"{ws.name}'s approval rule was changed.",
            workspace=ws,
            actor=request.user,
            target=rule,
        )
        messages.success(request, "Approval rule saved.")
        return redirect("reviews:rule", workspace_id=workspace_id)
    ctx = client_context(request, "services")
    ctx.update(
        {
            "rule": rule,
            "clients": clients,
            "staff": staff,
            "chosen_clients": set(rule.client_approvers.values_list("pk", flat=True)),
            "chosen_staff": set(rule.internal_approvers.values_list("pk", flat=True)),
            "can_manage": workspace_permission(request, "manage_client_profile"),
        }
    )
    return render(request, "reviews/rule.html", ctx)
