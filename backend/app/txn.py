"""Run code only once the surrounding database transaction has committed.

Used for side effects outside the database, like deleting a stored file: if the commit
fails, the file must still be there.
"""

import logging
from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)
_KEY = "vaultmind_after_commit"


def after_commit(db: Session, fn: Callable[[], None]) -> None:
    db.info.setdefault(_KEY, []).append(fn)


@event.listens_for(Session, "after_commit")
def _run(session: Session) -> None:
    for fn in session.info.pop(_KEY, []):
        try:
            fn()
        except Exception:
            log.exception("After-commit task failed")


@event.listens_for(Session, "after_rollback")
def _drop(session: Session) -> None:
    session.info.pop(_KEY, None)
