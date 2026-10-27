"""Operator commands.

    python -m app.cli create-admin --email ana@acme.com --name "Ana Rao" --team "IT"

The password is read from VAULTMIND_ADMIN_PASSWORD if set, otherwise prompted for.
"""

import argparse
import getpass
import os
import sys

from sqlalchemy import func, select

from .database import SessionLocal
from .migrations import upgrade_database
from .models import Position, Team, User
from .security import hash_password

MIN_PASSWORD = 12


def create_admin(email: str, name: str, team_name: str, password: str, title: str = "Administrator") -> User:
    email = email.strip().lower()
    if "@" not in email:
        raise ValueError("That doesn't look like an email address.")
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Use a password of at least {MIN_PASSWORD} characters.")
    if len(password.encode()) > 72:
        raise ValueError("Passwords are limited to 72 bytes (a bcrypt limit).")
    upgrade_database()
    with SessionLocal() as db:
        if db.scalar(select(User).where(func.lower(User.email) == email)):
            raise ValueError(f"An account for {email} already exists.")
        team = db.scalar(select(Team).where(Team.name == team_name))
        if team is None:
            team = Team(name=team_name, description="")
            db.add(team)
            db.flush()
        user = User(
            email=email,
            full_name=name.strip(),
            title=title,
            team_id=team.id,
            position=int(Position.EXECUTIVE),
            is_admin=True,
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-admin", help="create an administrator account")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--team", default="Administration")
    args = parser.parse_args(argv)

    password = os.environ.get("VAULTMIND_ADMIN_PASSWORD") or getpass.getpass("Password for the new admin: ")
    try:
        user = create_admin(args.email, args.name, args.team, password)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Created administrator {user.email} in team “{args.team}”.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
