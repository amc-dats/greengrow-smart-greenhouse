from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from greenhouse.permissions import GROWER_GROUP


class Command(BaseCommand):
    help = "Set a user's role: viewer (default for new sign-ups), grower, or admin. Use --create with --password to make the account."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("role", choices=["viewer", "grower", "admin"])
        parser.add_argument("--create", action="store_true", help="create the user if it does not exist")
        parser.add_argument("--password", help="password for --create (or set it for an existing user)")

    def handle(self, username, role, create, password, **_):
        User = get_user_model()
        user = User.objects.filter(username=username).first()
        if user is None:
            if not create:
                raise CommandError(f"no such user '{username}' (use --create --password ...)")
            if not password:
                raise CommandError("--create needs --password")
            user = User.objects.create_user(username=username, password=password)
        elif password:
            user.set_password(password)
        grower, _ = Group.objects.get_or_create(name=GROWER_GROUP)
        user.is_staff = user.is_superuser = role == "admin"
        user.save()
        if role == "grower":
            user.groups.add(grower)
        else:
            user.groups.remove(grower)
        self.stdout.write(f"{username} is now {role}")
