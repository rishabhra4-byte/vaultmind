# Release Checklist

Before every production release:

- All checks are green on the main branch and the change has one approving review.
- Database migrations are backwards compatible with the version currently running.
- Feature flags for unfinished work are switched off.
- The release note says what changed and how to roll back.

Release between 11am and 4pm on Monday to Thursday, never on a Friday. Watch the error dashboards for 30 minutes after deploying.
