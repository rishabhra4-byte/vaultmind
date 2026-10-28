import { useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../../api";
import { Avatar, Modal, Skeleton } from "../../components/bits";
import { IconPlus, IconSearch, IconUpload, IconUsers } from "../../components/icons";
import { useApp } from "../../state";
import type { AdminUser } from "../../types";

const PROVIDER: Record<string, string> = { google: "Google", microsoft: "Microsoft" };

export default function People() {
  const { meta, notify, user: me, refreshMeta } = useApp();
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [editing, setEditing] = useState<AdminUser | null>(null);

  const load = () =>
    api<AdminUser[]>("/api/admin/users")
      .then(setUsers)
      .catch(() => setUsers([]));
  useEffect(() => {
    load();
  }, []);

  async function update(u: AdminUser, change: Record<string, unknown>) {
    try {
      const updated = await api<AdminUser>(`/api/admin/users/${u.id}`, { method: "PATCH", json: change });
      setUsers((list) => list?.map((x) => (x.id === u.id ? updated : x)) ?? null);
      notify(`Updated ${u.full_name}`);
      return true;
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't update that person", "error");
      return false;
    }
  }

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (users ?? []).filter(
      (u) =>
        !q || u.full_name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q) || u.team_name.toLowerCase().includes(q),
    );
  }, [users, query]);

  return (
    <section>
      <div className="row between wrap admin-head">
        <h2>
          <IconUsers /> People
        </h2>
        <div className="row wrap">
          <button className="btn" onClick={() => setImporting(true)}>
            <IconUpload /> Import from a spreadsheet
          </button>
          <button className="btn primary" onClick={() => setAdding(true)}>
            <IconPlus /> Add a person
          </button>
        </div>
      </div>
      <p className="muted small section-note">
        Changing someone's team or position changes what they can open immediately. People synced from Google or Microsoft are
        updated at each sync.
      </p>
      <label className="search-box admin-search">
        <IconSearch />
        <input
          type="search"
          placeholder="Search by name, email or team"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search people"
        />
      </label>
      {users === null ? (
        <Skeleton rows={4} />
      ) : (
        <div className="table-wrap">
          <table className="people">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">Team</th>
                <th scope="col">Position</th>
                <th scope="col">Active</th>
                <th scope="col">
                  <span className="sr-only">Edit</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.map((u) => (
                <tr key={u.id}>
                  <td>
                    <span className="person-cell">
                      <Avatar name={u.full_name} team={u.team_name} size={32} />
                      <span>
                        <strong>{u.full_name}</strong>
                        <span className="muted small person-sub">
                          {u.email}
                          {u.title && ` · ${u.title}`}
                        </span>
                        <span className="badges">
                          {u.is_admin && <span className="pill">Admin</span>}
                          {u.directory_managed && <span className="pill">From directory</span>}
                          {!u.has_password && (
                            <span className="pill">
                              {PROVIDER[u.auth_provider] ? `${PROVIDER[u.auth_provider]} sign-in` : "Company sign-in"}
                            </span>
                          )}
                          {u.onboarding && <span className="pill">First week</span>}
                        </span>
                      </span>
                    </span>
                  </td>
                  <td>
                    <select
                      value={u.team_id}
                      onChange={(e) => update(u, { team_id: Number(e.target.value) })}
                      aria-label={`Team for ${u.full_name}`}
                    >
                      {meta?.teams.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select
                      value={u.position}
                      onChange={(e) => update(u, { position: Number(e.target.value) })}
                      aria-label={`Position for ${u.full_name}`}
                    >
                      {meta?.positions.map((p) => (
                        <option key={p.value} value={p.value}>
                          {p.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <label className="switch" title={u.id === me?.id ? "You can't deactivate yourself" : undefined}>
                      <input
                        type="checkbox"
                        checked={u.is_active}
                        disabled={u.id === me?.id}
                        onChange={(e) => update(u, { is_active: e.target.checked })}
                        aria-label={`${u.full_name} is active`}
                      />
                      <span />
                    </label>
                  </td>
                  <td>
                    <button className="btn small ghost" onClick={() => setEditing(u)} aria-label={`Edit ${u.full_name}`}>
                      Edit
                    </button>
                  </td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    Nobody matches “{query}”.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
      {adding && (
        <AddPerson
          onClose={() => setAdding(false)}
          onDone={(u) => {
            setAdding(false);
            setUsers((list) => [...(list ?? []), u]);
            notify(`Added ${u.full_name}`);
          }}
        />
      )}
      {editing && (
        <EditPerson
          person={editing}
          isMe={editing.id === me?.id}
          onClose={() => setEditing(null)}
          onSave={async (change) => {
            if (await update(editing, change)) setEditing(null);
          }}
        />
      )}
      {importing && (
        <ImportPeople
          onClose={() => setImporting(false)}
          onDone={() => {
            setImporting(false);
            load();
            refreshMeta();
          }}
        />
      )}
    </section>
  );
}

function AddPerson({ onClose, onDone }: { onClose: () => void; onDone: (u: AdminUser) => void }) {
  const { meta } = useApp();
  const companySignIn = (meta?.sso_providers.length ?? 0) > 0;
  const [form, setForm] = useState({
    email: "",
    full_name: "",
    title: "",
    team_id: meta?.teams[0]?.id ?? 0,
    position: 2,
    is_admin: false,
    onboarding: true,
  });
  const [usePassword, setUsePassword] = useState(!companySignIn);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (k: string, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const u = await api<AdminUser>("/api/admin/users", {
        method: "POST",
        json: { ...form, password: usePassword ? password : null },
      });
      onDone(u);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't add that person");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Add a person" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <div className="grid-2">
          <label className="field">
            <span>Full name</span>
            <input value={form.full_name} onChange={(e) => set("full_name", e.target.value)} required maxLength={120} />
          </label>
          <label className="field">
            <span>Work email</span>
            <input type="email" value={form.email} onChange={(e) => set("email", e.target.value)} required />
          </label>
        </div>
        <label className="field">
          <span>Job title</span>
          <input value={form.title} onChange={(e) => set("title", e.target.value)} maxLength={120} />
        </label>
        <div className="grid-2">
          <label className="field">
            <span>Team</span>
            <select value={form.team_id} onChange={(e) => set("team_id", Number(e.target.value))}>
              {meta?.teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Position</span>
            <select value={form.position} onChange={(e) => set("position", Number(e.target.value))}>
              {meta?.positions.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <fieldset className="field">
          <legend>How they sign in</legend>
          {companySignIn && (
            <label className="check">
              <input type="radio" name="signin" checked={!usePassword} onChange={() => setUsePassword(false)} />
              With their company account ({meta?.sso_providers.map((p) => p.label).join(" or ")})
            </label>
          )}
          <label className="check">
            <input type="radio" name="signin" checked={usePassword} onChange={() => setUsePassword(true)} />
            With a password you give them
          </label>
          {usePassword && (
            <input
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              required
              placeholder="At least 8 characters"
              aria-label="Starting password"
            />
          )}
        </fieldset>
        <label className="check">
          <input type="checkbox" checked={form.onboarding} onChange={(e) => set("onboarding", e.target.checked)} />
          Show them the first-week checklist
        </label>
        <label className="check">
          <input type="checkbox" checked={form.is_admin} onChange={(e) => set("is_admin", e.target.checked)} />
          Administrator (can open every document and change settings)
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy}>
            {busy ? "Adding…" : "Add person"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function EditPerson({
  person,
  isMe,
  onClose,
  onSave,
}: {
  person: AdminUser;
  isMe: boolean;
  onClose: () => void;
  onSave: (change: Record<string, unknown>) => void;
}) {
  const [name, setName] = useState(person.full_name);
  const [title, setTitle] = useState(person.title);
  const [admin, setAdmin] = useState(person.is_admin);
  const [onboarding, setOnboarding] = useState(person.onboarding);
  const [password, setPassword] = useState("");

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const change: Record<string, unknown> = {};
    if (name.trim() !== person.full_name) change.full_name = name.trim();
    if (title !== person.title) change.title = title;
    if (admin !== person.is_admin) change.is_admin = admin;
    if (onboarding !== person.onboarding) change.onboarding = onboarding;
    if (password) change.password = password;
    if (Object.keys(change).length === 0) return onClose();
    onSave(change);
  }

  return (
    <Modal title={`Edit ${person.full_name}`} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        {person.directory_managed && (
          <p className="fallback-note">This person is synced from your directory; the next sync may overwrite changes here.</p>
        )}
        <label className="field">
          <span>Full name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
        </label>
        <label className="field">
          <span>Job title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} />
        </label>
        <label className="field">
          <span>{person.has_password ? "Set a new password" : "Give them a password"}</span>
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            placeholder="Leave empty to keep it as it is"
          />
        </label>
        <label className="check">
          <input type="checkbox" checked={onboarding} onChange={(e) => setOnboarding(e.target.checked)} />
          Show the first-week checklist
        </label>
        <label className="check" title={isMe ? "You can't remove your own admin access" : undefined}>
          <input type="checkbox" checked={admin} disabled={isMe} onChange={(e) => setAdmin(e.target.checked)} />
          Administrator
        </label>
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary">Save</button>
        </div>
      </form>
    </Modal>
  );
}

interface ImportResult {
  dry_run: boolean;
  created: number;
  updated: number;
  unchanged: number;
  errors: number;
  new_teams: string[];
  rows: { row: number; email: string; action: string; detail: string }[];
}

const TEMPLATE =
  "email,name,title,team,position,admin\npriya.n@example.com,Priya Nair,Backend Engineer,Engineering,Associate,no\n";

function ImportPeople({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { notify } = useApp();
  const [file, setFile] = useState<File | null>(null);
  const [createTeams, setCreateTeams] = useState(false);
  const [preview, setPreview] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  async function run(dryRun: boolean) {
    if (!file) return;
    setBusy(true);
    setError("");
    const body = new FormData();
    body.append("file", file);
    body.append("dry_run", String(dryRun));
    body.append("create_teams", String(createTeams));
    try {
      const result = await api<ImportResult>("/api/admin/users/import", { method: "POST", body });
      if (dryRun) setPreview(result);
      else {
        notify(`Added ${result.created}, updated ${result.updated}`);
        onDone();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't read that file");
    } finally {
      setBusy(false);
    }
  }

  function downloadTemplate() {
    const url = URL.createObjectURL(new Blob([TEMPLATE], { type: "text/csv" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "vaultmind-people.csv" });
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <Modal title="Import people from a spreadsheet" onClose={onClose}>
      <div className="stack">
        <p className="muted">
          Save the sheet as CSV with the columns <code>email, name, title, team, position, admin</code>. New people sign in with
          their company account; existing people are updated. Nothing changes until you press Import.{" "}
          <button className="link-btn" onClick={downloadTemplate}>
            <u>Download a template</u>
          </button>
        </p>
        <button type="button" className="dropzone" onClick={() => inputRef.current?.click()}>
          <span className="dz-icon">
            <IconUpload />
          </span>
          <strong>{file ? file.name : "Choose a CSV file"}</strong>
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          hidden
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            setPreview(null);
          }}
        />
        <label className="check">
          <input
            type="checkbox"
            checked={createTeams}
            onChange={(e) => {
              setCreateTeams(e.target.checked);
              setPreview(null);
            }}
          />
          Create teams that don't exist yet
        </label>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {preview && (
          <div className="import-preview">
            <p>
              <strong>{preview.created}</strong> to add, <strong>{preview.updated}</strong> to update, {preview.unchanged}{" "}
              unchanged
              {preview.errors > 0 && (
                <>
                  , <strong className="danger-text">{preview.errors} with problems</strong>
                </>
              )}
              {preview.new_teams.length > 0 && `. New teams: ${preview.new_teams.join(", ")}`}.
            </p>
            <div className="table-wrap">
              <table className="people compact-table">
                <thead>
                  <tr>
                    <th scope="col">Row</th>
                    <th scope="col">Email</th>
                    <th scope="col">What happens</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.rows
                    .filter((r) => r.action !== "unchanged")
                    .map((r) => (
                      <tr key={r.row} className={r.action === "error" ? "row-error" : ""}>
                        <td>{r.row}</td>
                        <td>{r.email}</td>
                        <td>
                          {r.action === "create" ? "Add" : r.action === "update" ? "Update" : "Problem"}
                          {r.detail && <span className="muted small">: {r.detail}</span>}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          {preview ? (
            <button
              className="btn primary"
              onClick={() => run(false)}
              disabled={busy || preview.errors > 0 || preview.created + preview.updated === 0}
            >
              {busy ? "Importing…" : "Import"}
            </button>
          ) : (
            <button className="btn primary" onClick={() => run(true)} disabled={busy || !file}>
              {busy ? "Checking…" : "Preview"}
            </button>
          )}
        </div>
      </div>
    </Modal>
  );
}
