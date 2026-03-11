"""
Test PostgreSQL connection. On password auth failure, prints the exact command
to set the postgres user password to match .env PGPASSWORD.
"""
from django.core.management.base import BaseCommand
from django.db import connection
from django.conf import settings


class Command(BaseCommand):
    help = "Check PostgreSQL connection; on failure, show how to fix password."

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        host, user, pwd = db.get("HOST"), db.get("USER"), (db.get("PASSWORD") or "")
        pwd_len = len(pwd)

        try:
            connection.ensure_connection()
            self.stdout.write(self.style.SUCCESS("OK: PostgreSQL connection succeeded."))
        except Exception as e:
            err = str(e).lower()
            if "password authentication failed" in err:
                self.stdout.write(
                    self.style.ERROR(
                        "PostgreSQL rejected the password. Django is using:\n"
                        f"  HOST={host!r} USER={user!r} PASSWORD length={pwd_len}\n"
                    )
                )
                if pwd_len == 0:
                    self.stdout.write(
                        self.style.ERROR(
                            "PGPASSWORD is empty. Set PGPASSWORD=your_password in backend/.env, then run:\n"
                        )
                    )
                    self.stdout.write(
                        '  sudo -u postgres psql -c "ALTER USER postgres PASSWORD \'your_password\';"\n'
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            "Set the postgres user password to the same value as PGPASSWORD in .env:\n"
                        )
                    )
                    # Escape single quotes in password for use inside single-quoted SQL
                    pwd_escaped = pwd.replace("\\", "\\\\").replace("'", "''")
                    self.stdout.write(
                        f'  sudo -u postgres psql -c "ALTER USER postgres PASSWORD \'{pwd_escaped}\';"\n'
                    )
            else:
                self.stdout.write(self.style.ERROR(f"Connection failed: {e}"))
            raise SystemExit(1)
