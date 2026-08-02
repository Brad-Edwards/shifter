"""Workspace membership routes mounted at `/api/v1/workspaces/`."""

from django.urls import path

from workspaces.api.views import (
    MembershipLeaveView,
    MembershipListAddView,
    MembershipRemoveView,
    MembershipRoleView,
    OrganizationListView,
    OrganizationProfileView,
    PrincipalWorkspaceContextView,
    SelfMembershipView,
)

app_name = "workspaces"

urlpatterns = [
    # Static `context/` and `organizations/` prefixes are declared before the
    # `<uuid:workspace_uuid>` routes; neither is a valid UUID, so the ordering is
    # defensive, not load-bearing.
    path("context/", PrincipalWorkspaceContextView.as_view(), name="principal-context"),
    path("organizations/", OrganizationListView.as_view(), name="organization-list"),
    path(
        "organizations/<uuid:organization_uuid>/",
        OrganizationProfileView.as_view(),
        name="organization-detail",
    ),
    path("<uuid:workspace_uuid>/membership/", SelfMembershipView.as_view(), name="membership-self"),
    path("<uuid:workspace_uuid>/memberships/", MembershipListAddView.as_view(), name="memberships"),
    path("<uuid:workspace_uuid>/memberships/leave/", MembershipLeaveView.as_view(), name="memberships-leave"),
    path(
        "<uuid:workspace_uuid>/memberships/<int:user_id>/role/",
        MembershipRoleView.as_view(),
        name="memberships-role",
    ),
    path(
        "<uuid:workspace_uuid>/memberships/<int:user_id>/remove/",
        MembershipRemoveView.as_view(),
        name="memberships-remove",
    ),
]
