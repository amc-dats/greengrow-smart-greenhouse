"""Browser pages: register, sign in/out, and a small greenhouse console. The console only calls the REST API (same origin, session cookie, CSRF token)."""
from django.contrib.auth import get_user_model, login, password_validation
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .permissions import role_of


@require_http_methods(["GET", "POST"])
def register(request):
    """Open sign-up. A new account is always a viewer; only an administrator can promote it (manage.py set_role)."""
    if request.user.is_authenticated:
        return redirect("console")
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
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
