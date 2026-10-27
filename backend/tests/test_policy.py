from datetime import UTC, datetime, timedelta

from app.models import Classification as C
from app.models import Document, DocumentShare, Team, User
from app.models import Position as P
from app.policy import can_approve, can_manage, can_view, grant_is_active

ENG, HR, SALES = Team(id=1, name="Engineering"), Team(id=2, name="Human Resources"), Team(id=3, name="Sales")


def user(uid=10, team=ENG, pos=P.ASSOCIATE, admin=False, active=True) -> User:
    return User(
        id=uid,
        team_id=team.id,
        team=team,
        position=int(pos),
        is_admin=admin,
        is_active=active,
        email=f"u{uid}@x",
        full_name=f"User {uid}",
        title="",
    )


def doc(cls=C.INTERNAL, team=ENG, min_pos=P.INTERN, shares=(), uploader=None, did=100) -> Document:
    d = Document(
        id=did,
        title="Doc",
        owner_team_id=team.id,
        owner_team=team,
        classification=cls.value,
        min_position=int(min_pos),
        uploaded_by=uploader,
        discoverable=True,
    )
    d.shares = [DocumentShare(team_id=t.id) for t in shares]
    return d


def test_public_is_open_to_everyone():
    assert can_view(user(team=SALES, pos=P.INTERN), doc(C.PUBLIC)).code == "public"


def test_internal_is_limited_to_owning_and_shared_teams():
    d = doc(C.INTERNAL, team=ENG, shares=[HR])
    assert can_view(user(team=ENG), d)
    assert can_view(user(team=HR), d).code == "shared"
    denied = can_view(user(team=SALES), d)
    assert not denied and denied.code == "team"
    assert "Engineering" in denied.reason


def test_confidential_needs_seniority_even_inside_the_team():
    d = doc(C.CONFIDENTIAL, min_pos=P.MANAGER)
    denied = can_view(user(pos=P.SENIOR), d)
    assert not denied and denied.code == "position"
    assert "Manager" in denied.reason and "Senior" in denied.reason
    assert can_view(user(pos=P.MANAGER), d)


def test_restricted_requires_a_named_grant_even_for_executives():
    d = doc(C.RESTRICTED, uploader=1)
    assert not can_view(user(pos=P.EXECUTIVE), d)
    assert can_view(user(uid=1), d).code == "uploader"
    assert can_view(user(uid=2), d, granted_doc_ids={d.id}).code == "grant"


def test_executives_see_other_teams_non_restricted_docs():
    assert can_view(user(team=SALES, pos=P.EXECUTIVE), doc(C.CONFIDENTIAL, team=HR, min_pos=P.MANAGER)).code == "executive"


def test_admin_and_inactive_users():
    assert can_view(user(team=SALES, admin=True), doc(C.RESTRICTED)).code == "admin"
    assert not can_view(user(active=False), doc(C.PUBLIC))


def test_grant_overrides_team_and_position():
    d = doc(C.CONFIDENTIAL, team=HR, min_pos=P.DIRECTOR)
    assert not can_view(user(team=ENG, pos=P.INTERN), d)
    assert can_view(user(team=ENG, pos=P.INTERN), d, {d.id})


def test_who_can_manage():
    d = doc(C.INTERNAL, team=ENG)
    assert can_manage(user(pos=P.MANAGER), d)
    assert not can_manage(user(pos=P.SENIOR), d)
    assert not can_manage(user(team=HR, pos=P.DIRECTOR), d)
    assert can_manage(user(uid=5, pos=P.INTERN), doc(uploader=5))


def test_who_can_approve_requests():
    d = doc(C.CONFIDENTIAL, team=HR, min_pos=P.MANAGER)
    hr_manager = user(uid=1, team=HR, pos=P.MANAGER)
    assert can_approve(hr_manager, d, requester_id=2)
    assert not can_approve(hr_manager, d, requester_id=1), "nobody approves their own request"
    assert not can_approve(user(uid=3, team=HR, pos=P.SENIOR), d, 2)
    assert not can_approve(user(uid=4, team=ENG, pos=P.DIRECTOR), d, 2), "authority is per owning team"

    restricted = doc(C.RESTRICTED, team=HR)
    assert not can_approve(hr_manager, restricted, 2)
    assert can_approve(user(uid=6, team=HR, pos=P.DIRECTOR), restricted, 2)


def test_grant_expiry():
    now = datetime.now(UTC)
    assert grant_is_active(now + timedelta(hours=1), False)
    assert not grant_is_active(now - timedelta(seconds=1), False)
    assert not grant_is_active(None, True)
    assert grant_is_active((now + timedelta(days=1)).replace(tzinfo=None), False), "naive datetimes from SQLite"
