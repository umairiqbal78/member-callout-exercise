"""
Rule 1 permission classes.

DEFAULT_PERMISSION_CLASSES is DenyAll — every view must declare its own
permission class(es) explicitly. A CI test walks every URL and fails if any
view is missing an explicit permission class.
"""

from rest_framework.permissions import BasePermission


class DenyAll(BasePermission):
    """
    Default permission: deny everything.

    This is set as DEFAULT_PERMISSION_CLASSES so that new endpoints are
    automatically closed. A developer must explicitly open each view.
    """

    message = "Permission explicitly required."

    def has_permission(self, request, view):
        return False


class IsAuthenticated(BasePermission):
    """Base: request must carry a valid auth token."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


class IsMember(IsAuthenticated):
    """
    The caller must be authenticated and have a Member row linked to their account.

    This is the base permission for all member-facing endpoints.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return hasattr(request.user, "member")


class IsLeaderOfLocal(IsAuthenticated):
    """
    The caller must be authenticated, have a Member row, and hold an active
    leadership_roles row for their own local.

    "Active" means revoked_at IS NULL.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        user = request.user
        if not hasattr(user, "member"):
            return False
        member = user.member
        return member.leadership_roles.filter(
            local_id=member.local_id,
            revoked_at__isnull=True,
        ).exists()


class IsLeaderOfAnyLocal(IsAuthenticated):
    """
    The caller must be authenticated, have a Member row, and hold an active
    leadership_roles row for at least one local.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        user = request.user
        if not hasattr(user, "member"):
            return False
        return user.member.leadership_roles.filter(
            revoked_at__isnull=True,
        ).exists()

