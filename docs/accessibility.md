# Accessibility statement

VaultMind aims to meet **WCAG 2.2 level AA** in its web app. This page says how that's checked,
what's known not to be checked yet, and how to report a problem.

## How it's checked

On every change, CI's browser audit ([`e2e/audit.js`](../e2e/audit.js)) runs against the real
app in Chromium, Firefox and WebKit:

- **An axe scan** (WCAG 2 A and AA rules) of every page and of open panels and dialogs, at eight
  screen widths from 320 to 1920 pixels, in the light and the dark theme.
- **Layout checks**: no sideways scrolling down to 320 pixels, and touch targets of at least 24
  pixels.
- **Keyboard checks** of the main flows: starter prompts, "Skip to content", and dialogs.

Before each release, a deeper review ([`e2e/a11y-review.js`](../e2e/a11y-review.js)) is run by
hand in Chromium: everything reachable and usable with a keyboard and a visible focus ring;
focus moving into dialogs and panels, staying there, and returning to the button that opened
them on Escape; focus never hidden behind sticky headers; reduced motion respected; text that
still fits with increased letter and line spacing.

What this covers in the parts added on 31 October 2026: the "Why these sources" disclosure is a
native `<details>` element; disagreements between documents are a labelled note, not colour
alone; the "Answer from" filters and the account dialog's controls have visible labels; status
badges pair colour with words. An axe scan of an answer with its sources open and the filters
open, in light and dark, on a desktop and a phone, found no violations.

## Known gaps

| Gap | Why | Plan |
|---|---|---|
| No pass with a real screen reader (NVDA, JAWS, VoiceOver, TalkBack) | Automated checks and accessibility-tree snapshots can't judge how it sounds | A manual pass of the main flows before general availability |
| Not tried on real phones and tablets | Checked only in desktop browsers at phone sizes | Test on an iPhone and an Android phone |
| Answers appear word by word | People using screen readers hear the finished answer (the conversation is a polite live region), but the typing effect can't be switched off except by the system's reduced-motion setting | A setting to turn it off |
| Documents' own contents | VaultMind shows the text of uploaded files as it is; their accessibility depends on the originals | |

## Reporting a problem

Open an issue on the GitHub repository with the page, what you were trying to do, and the
browser and assistive technology you use. Accessibility problems that block someone from
doing their job are treated as bugs to fix first.
