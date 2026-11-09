import hmac

from django.conf import settings
from rest_framework.permissions import SAFE_METHODS, BasePermission


def _matches(supplied: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())


class ReadOnlyOrApiKey(BasePermission):
    """Reads are open on the demo network; every write needs the X-API-Key header."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return _matches(request.headers.get("X-API-Key", ""), settings.API_KEY)


class SimToken(BasePermission):
    """The edge node reads the simulated sensors with its own token (X-Sim-Token)."""

    def has_permission(self, request, view):
        return _matches(request.headers.get("X-Sim-Token", ""), settings.SIM_TOKEN)
