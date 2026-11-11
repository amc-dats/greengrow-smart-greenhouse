"""Authentication and roles.

People sign in with a username and password (Django session). Machines (scripts, the module's curl examples) may still use the
shared `X-API-Key`, which acts as an administrator. Roles:
  viewer  any registered user: may read.
  grower  member of the "grower" group: may also queue commands, feed, and generate/approve/reject suggestions.
  admin   staff or superuser (or the API key): may also inject simulator faults.
New registrations are always viewers; an administrator promotes a user with `manage.py set_role <username> grower|admin|viewer`.
"""
import hmac

from django.conf import settings
from rest_framework.authentication import BaseAuthentication, SessionAuthentication
from rest_framework.permissions import SAFE_METHODS, BasePermission

GROWER_GROUP = "grower"


def _matches(supplied: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())


class ServiceUser:
    """The principal for a request authenticated with the shared API key."""
    is_authenticated = True
    is_staff = True
    is_superuser = True
    username = "api-key"
    pk = None

    def __str__(self):
        return self.username


class ApiKeyAuthentication(BaseAuthentication):
    """A wrong or missing key is simply not authenticated (the request is then refused by the permission class)."""

    def authenticate(self, request):
        key = request.headers.get("X-API-Key", "")
        if key and _matches(key, settings.API_KEY):
            return ServiceUser(), None
        return None


class CsrfSessionAuthentication(SessionAuthentication):
    """Session login for browsers; DRF enforces the CSRF token on unsafe methods."""


ROLES = ("viewer", "grower", "admin")


def set_user_role(user, role: str) -> None:
    """Make `user` a viewer, grower or admin (the one place roles are changed)."""
    from django.contrib.auth.models import Group
    if role not in ROLES:
        raise ValueError(f"unknown role '{role}'")
    grower, _ = Group.objects.get_or_create(name=GROWER_GROUP)
    user.is_staff = user.is_superuser = role == "admin"
    user.save()
    if role == "grower":
        user.groups.add(grower)
    else:
        user.groups.remove(grower)


def role_of(user) -> str:
    if not getattr(user, "is_authenticated", False):
        return "anonymous"
    if user.is_staff or user.is_superuser:
        return "admin"
    if user.groups.filter(name=GROWER_GROUP).exists():
        return "grower"
    return "viewer"


class SignedIn(BasePermission):
    """Default: any signed-in user may read; changing anything needs the grower role (or admin)."""

    def has_permission(self, request, view):
        role = role_of(request.user)
        if role == "anonymous":
            return False
        return request.method in SAFE_METHODS or role in ("grower", "admin")


class AdminOnly(BasePermission):
    """Fault injection and other test-only controls."""

    def has_permission(self, request, view):
        return role_of(request.user) == "admin"


class SimToken(BasePermission):
    """The edge node reads the simulated sensors with its own token (X-Sim-Token)."""

    def has_permission(self, request, view):
        return _matches(request.headers.get("X-Sim-Token", ""), settings.SIM_TOKEN)
