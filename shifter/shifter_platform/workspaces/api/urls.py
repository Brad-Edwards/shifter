"""Workspace membership routes mounted at `/api/v1/workspaces/`."""

from django.urls import path

from workspaces.api.views import (
    MembershipLeaveView,
    MembershipListAddView,
    MembershipRemoveView,
    MembershipRoleView,
    SelfMembershipView,
)

app_name = "workspaces"

urlpatterns = [
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
