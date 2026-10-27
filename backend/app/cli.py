"""Operator commands for a real (non-demo) deployment.

    python -m app.cli create-admin --email ana@acme.com --name "Ana Rao" --team "IT"
    python -m app.cli create-user  --email ravi@acme.com --name "Ravi Kumar" --team Engineering \\
                                   --position senior --title "Backend Engineer"
    python -m app.cli set-password --email ravi@acme.com

Passwords are read from VAULTMIND_PASSWORD (or VAULTMIND_ADMIN_PASSWORD for create-admin) if
set, otherwise prompted for, so they never appear in the shell history or the process list.
Teams that don't exist yet are created. Positions, teams and access can be changed later in
the admin screen.
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
POSITIONS = {p.name.lower(): p for p in Position}


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Use a password of at least {MIN_PASSWORD} characters.")
    if len(password.encode()) > 72:
        raise ValueError("Passwords are limited to 72 bytes (a bcrypt limit).")


def _clean_email(email: str) -> str:
    email = email.strip().lower()
    if "@" not in email or len(email) > 255:
        raise ValueError("That doesn't look like an email address.")
    return email


def create_user(
    email: str,
    name: str,
    team_name: str,
    password: str,
    *,
    position: Position = Position.ASSOCIATE,
    title: str = "",
    is_admin: bool = False,
) -> User:
    email = _clean_email(email)
    if not name.strip():
        raise ValueError("A name is required.")
    _check_password(password)
    upgrade_database()
    with SessionLocal() as db:
        if db.scalar(select(User).where(func.lower(User.email) == email)):
            raise ValueError(f"An account for {email} already exists.")
        team = db.scalar(select(Team).where(Team.name == team_name.strip()))
        if team is None:
            team = Team(name=team_name.strip(), description="")
            db.add(team)
            db.flush()
        user = User(
            email=email,
            full_name=name.strip(),
            title=title.strip() or position.label,
            team_id=team.id,
            position=int(position),
            is_admin=is_admin,
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def create_admin(email: str, name: str, team_name: str, password: str, title: str = "Administrator") -> User:
    return create_user(email, name, team_name, password, position=Position.EXECUTIVE, title=title, is_admin=True)


def set_password(email: str, password: str) -> User:
    email = _clean_email(email)
    _check_password(password)
    upgrade_database()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == email))
        if user is None:
            raise ValueError(f"There is no account for {email}.")
        user.password_hash = hash_password(password)
        db.commit()
        db.refresh(user)
        return user


def _password(env: str, prompt: str) -> str:
    return os.environ.get(env) or os.environ.get("VAULTMIND_PASSWORD") or getpass.getpass(prompt)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="VaultMind operator commands")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-admin", help="create an administrator account")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--team", default="Administration")

    p = sub.add_parser("create-user", help="create an employee account")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--team", required=True, help="created if it doesn't exist")
    p.add_argument("--position", required=True, choices=list(POSITIONS))
    p.add_argument("--title", default="", help="job title shown in the app")

    p = sub.add_parser("set-password", help="set a new password for an existing account")
    p.add_argument("--email", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "create-admin":
            password = _password("VAULTMIND_ADMIN_PASSWORD", "Password for the new admin: ")
            user = create_admin(args.email, args.name, args.team, password)
            print(f"Created administrator {user.email} in team “{args.team}”.")
        elif args.command == "create-user":
            password = _password("VAULTMIND_PASSWORD", f"Password for {args.email}: ")
            user = create_user(args.email, args.name, args.team, password, position=POSITIONS[args.position], title=args.title)
            print(f"Created {user.email} ({Position(user.position).label}) in team “{args.team}”.")
        else:
            password = _password("VAULTMIND_PASSWORD", f"New password for {args.email}: ")
            user = set_password(args.email, password)
            print(f"Password updated for {user.email}.")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
