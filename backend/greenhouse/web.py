"""Browser pages: register, sign in/out, and a small greenhouse console. The console only calls the REST API (same origin, session cookie, CSRF token)."""
from django.contrib.auth import get_user_model, login, password_validation
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .permissions import role_of

LOGIN_MAX_FAILURES = 5          # failed sign-ins from one address ...
LOGIN_WINDOW_S = 15 * 60        # ... within this window lock sign-in for the rest of it
REGISTER_MAX_PER_HOUR = 10      # new accounts from one address per hour


def client_ip(request) -> str:
    """The caller's address; behind the public tunnel it arrives in CF-Connecting-IP."""
    return request.headers.get("CF-Connecting-IP") or request.META.get("REMOTE_ADDR", "?")


def _too_many(key: str, limit: int) -> bool:
    return cache.get(key, 0) >= limit


def _count(key: str, window: int) -> None:
    cache.add(key, 0, window)
    cache.incr(key)


class ThrottledLoginView(LoginView):
    """Sign-in with a lock-out: too many failures from one address are refused until the window ends."""

    redirect_authenticated_user = True

    def post(self, request, *a, **kw):
        key = f"loginfail:{client_ip(request)}"
        if _too_many(key, LOGIN_MAX_FAILURES):
            return HttpResponse("Too many failed sign-ins. Try again in 15 minutes.", status=429)
        return super().post(request, *a, **kw)

    def form_invalid(self, form):
        _count(f"loginfail:{client_ip(self.request)}", LOGIN_WINDOW_S)
        return super().form_invalid(form)


@require_http_methods(["GET", "POST"])
def register(request):
    """Open sign-up. A new account is always a viewer; only an administrator can promote it (manage.py set_role)."""
    if request.user.is_authenticated:
        return redirect("console")
    key = f"register:{client_ip(request)}"
    if request.method == "POST" and _too_many(key, REGISTER_MAX_PER_HOUR):
        return HttpResponse("Too many sign-ups from this address. Try again later.", status=429)
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        _count(key, 3600)
        user = form.save()
        login(request, user)
        return redirect("console")
    return render(request, "registration/register.html", {"form": form})


@login_required
def console(request):
    return render(request, "greenhouse/console.html", {"role": role_of(request.user)})


@login_required
@require_http_methods(["GET", "POST"])
def users(request):
    """Administrators only: list accounts and reset a user's password (existing sessions of that user are ended)."""
    if role_of(request.user) != "admin":
        return HttpResponseForbidden("administrators only")
    message, error = "", ""
    if request.method == "POST":
        target = get_user_model().objects.filter(pk=request.POST.get("user_id")).first()
        pw = request.POST.get("new_password", "")
        if target is None:
            error = "No such user."
        else:
            try:
                password_validation.validate_password(pw, target)
            except ValidationError as e:
                error = " ".join(e.messages)
            else:
                target.set_password(pw)
                target.save()
                for s in Session.objects.all():
                    if s.get_decoded().get("_auth_user_id") == str(target.pk):
                        s.delete()
                message = f"Password reset for {target.username}."
    rows = [{"id": u.pk, "username": u.username, "role": role_of(u)} for u in get_user_model().objects.order_by("username")]
    return render(request, "greenhouse/users.html", {"rows": rows, "message": message, "error": error})
