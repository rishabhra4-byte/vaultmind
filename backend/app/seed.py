"""Seed a demo company so the app is useful on first run.

Run directly with `python -m app.seed` or automatically on startup when the database is
empty and VAULTMIND_SEED_DEMO_DATA is true (the default).
"""

import random
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AccessGrant,
    AccessRequest,
    AuditEvent,
    Classification,
    Document,
    Position,
    RequestStatus,
    Team,
    User,
)
from .security import hash_password
from .services import ingest_document
from .text import extractive_summary

DEMO_PASSWORD = "demo1234"
DOMAIN = "northwind.example"

TEAMS = [
    ("Engineering", "Builds and runs the Northwind platform"),
    ("Human Resources", "People, hiring and culture"),
    ("Finance", "Accounting, payments and planning"),
    ("Sales", "Customer acquisition and accounts"),
    ("Leadership", "Executive team"),
]

# key, full name, title, team, position, admin
USERS = [
    ("aarav", "Aarav Mehta", "Software Engineering Intern", "Engineering", Position.INTERN, False),
    ("karan", "Karan Singh", "Account Executive", "Sales", Position.ASSOCIATE, False),
    ("neha", "Neha Kapoor", "HR Associate", "Human Resources", Position.ASSOCIATE, False),
    ("priya", "Priya Sharma", "Senior Backend Engineer", "Engineering", Position.SENIOR, False),
    ("dev", "Dev Patel", "IT Administrator", "Engineering", Position.SENIOR, True),
    ("rohan", "Rohan Iyer", "Engineering Manager", "Engineering", Position.MANAGER, False),
    ("ananya", "Ananya Gupta", "Finance Manager", "Finance", Position.MANAGER, False),
    ("vikram", "Vikram Rao", "Director of People", "Human Resources", Position.DIRECTOR, False),
    ("meera", "Meera Nair", "Chief Executive Officer", "Leadership", Position.EXECUTIVE, False),
]

# Who has been reading what recently (drives "popular with your team" recommendations).
VIEWS = {
    "priya": ["Backend Onboarding Guide", "API Design Guidelines", "System Architecture Overview", "Incident Response Runbook"],
    "rohan": ["Backend Onboarding Guide", "Incident Response Runbook", "Salary Bands 2026", "API Design Guidelines"],
    "dev": ["IT Security Basics", "System Architecture Overview", "Backend Onboarding Guide"],
    "neha": ["Recruitment Process", "Holiday Calendar 2026", "Employee Handbook 2026"],
    "karan": ["Sales Playbook", "Expense Reimbursement Policy"],
    "ananya": ["Vendor Payment Process", "Q3 2026 Financial Report"],
}


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") + ".md"


def seed(db: Session) -> bool:
    """Populate an empty database. Returns False if data already exists."""
    if db.scalar(select(User.id).limit(1)) is not None:
        return False

    from .seed_docs import DOCS  # local import keeps app start-up light when not seeding

    rng = random.Random(42)
    now = datetime.now(UTC)

    teams = {name: Team(name=name, description=desc) for name, desc in TEAMS}
    db.add_all(teams.values())
    db.flush()

    pw = hash_password(DEMO_PASSWORD)
    users = {}
    for key, name, title, team, position, admin in USERS:
        users[key] = User(
            email=f"{key}@{DOMAIN}",
            full_name=name,
            title=title,
            team_id=teams[team].id,
            position=int(position),
            is_admin=admin,
            password_hash=pw,
        )
    db.add_all(users.values())
    db.flush()

    docs: dict[str, Document] = {}
    for spec in DOCS:
        cls: Classification = spec["cls"]
        result = ingest_document(
            db,
            uploader=users[spec["uploader"]],
            filename=_slug(spec["title"]),
            data=spec["body"].encode(),
            mime_type="text/markdown",
            title=spec["title"],
            owner_team_id=teams[spec["team"]].id,
            classification=cls,
            min_position=spec.get("min_pos"),
            share_team_ids=[teams[t].id for t in spec.get("shares", [])],
            tags=spec["tags"],
            summary=spec.get("summary") or extractive_summary(spec["body"]),
        )
        doc = result.document
        doc.created_at = doc.updated_at = now - timedelta(days=spec["age"], hours=rng.randint(0, 20))
        docs[doc.title] = doc

    events = []
    for key, titles in VIEWS.items():
        for title in titles:
            for _ in range(rng.randint(1, 3)):
                events.append(
                    AuditEvent(
                        actor_id=users[key].id,
                        action="document.view",
                        target_type="document",
                        target_id=docs[title].id,
                        detail={"title": title},
                        created_at=now - timedelta(days=rng.randint(1, 20), hours=rng.randint(0, 23)),
                    )
                )

    # A couple of pending requests so approvers have something in their inbox...
    hiring = docs["Engineering Hiring Plan H1 2027"]
    bands = docs["Salary Bands 2026"]
    db.add_all(
        [
            AccessRequest(
                document_id=hiring.id,
                requester_id=users["priya"].id,
                duration_days=14,
                reason="I'm joining the interview panel for the new payments team and need the role breakdown to plan interview loops.",
                created_at=now - timedelta(hours=20),
            ),
            AccessRequest(
                document_id=bands.id,
                requester_id=users["karan"].id,
                duration_days=3,
                reason="Preparing a market comparison for a candidate referral; I need the Account Executive band.",
                created_at=now - timedelta(hours=5),
            ),
        ]
    )
    # ...and one that was approved earlier, so the grants view isn't empty.
    perf = docs["Performance Review Guidelines for Managers"]
    db.add(
        AccessRequest(
            document_id=perf.id,
            requester_id=users["neha"].id,
            duration_days=7,
            reason="Helping managers schedule October review conversations and need the timeline and rules.",
            status=RequestStatus.APPROVED,
            decided_by=users["vikram"].id,
            decision_note="Approved for the review cycle.",
            created_at=now - timedelta(days=2, hours=3),
            decided_at=now - timedelta(days=2),
        )
    )
    db.add(
        AccessGrant(
            document_id=perf.id,
            user_id=users["neha"].id,
            granted_by=users["vikram"].id,
            expires_at=now + timedelta(days=5),
            created_at=now - timedelta(days=2),
        )
    )
    events.append(
        AuditEvent(
            actor_id=users["vikram"].id,
            action="access.approve",
            target_type="document",
            target_id=perf.id,
            detail={"title": perf.title, "requester": "Neha Kapoor", "duration_days": 7},
            created_at=now - timedelta(days=2),
        )
    )
    db.add_all(events)
    db.commit()
    return True


if __name__ == "__main__":
    from .database import Base, SessionLocal, engine

    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        print("Seeded demo data" if seed(session) else "Database already has data; nothing to do")
