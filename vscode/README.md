# VaultMind for VS Code

Ask your company's documents without leaving the editor. Everything respects the same
permissions as the VaultMind website: you only ever get answers from documents you can open.

- **Ask** (the VaultMind icon in the activity bar): chat with your company's documents. Answers
  stream in, say where they came from (AI answer, matching passages, or a verified answer and who
  verified it), and cite their sources; click a number to open the paragraph in the browser.
- **Ask about a selection**: select code or text, right-click → **Ask VaultMind about this**, for
  example "Does this follow our API guidelines?". The selection goes with the question.
- **Documents for this project**: the company documents that matter for the open folder, found
  from its README, `package.json` and folders (or the `topics` you set, below). Search all
  documents with the 🔍 button.
- **Research quests**: **VaultMind: New research quest** creates a `*.quest.md` with a brief and a
  list of questions. Press ▶ in the editor title bar: VaultMind answers each question from the
  documents and writes a `*.report.md` next to it, every claim cited, with a summary of what the
  documents did and didn't answer.
- **Project rules**: a `.vaultmind.json` at the project root narrows which documents are used and
  what the project is about:

  ```json
  {
    "filters": { "team_id": 2, "updated_within_days": 365 },
    "topics": ["incident response", "on-call", "API guidelines"]
  }
  ```

  Filters: `team_id`, `classification` (`public` … `restricted`), `source` (`"uploaded"` or a source
  number), `owner_id`, `updated_within_days`. They only ever narrow what you can open.

## Signing in

**VaultMind: Sign in** asks which VaultMind to use (your company's address, the project run
locally at `http://localhost:5173`, or the public demo), then opens a page in your browser showing
a code. Check it matches the one in VS Code (it's also on your clipboard) and approve. VS Code
never sees your password, and the session appears as "VS Code extension" in your list of signed-in
sessions on the website, where you can end it. It ends like any other session (sign out, password
change, time limits your company set).

## Build and install

```bash
cd vscode
npm ci
npm test          # unit tests; set VAULTMIND_URL=http://localhost:8000 to also test against a running server
npm run package   # → vaultmind-vscode.vsix
code --install-extension vaultmind-vscode.vsix
```

Or in VS Code: Extensions → ⋯ → **Install from VSIX…**.
