import { useEffect, useState } from "react";
import { api, ApiError } from "../../api";
import { Modal, Skeleton, TeamChip } from "../../components/bits";
import { IconPlus, IconUsers } from "../../components/icons";
import { useApp } from "../../state";
import type { AdminTeam } from "../../types";

export default function Teams() {
  const { notify, refreshMeta } = useApp();
  const [teams, setTeams] = useState<AdminTeam[] | null>(null);
  const [editing, setEditing] = useState<AdminTeam | "new" | null>(null);
  const [deleting, setDeleting] = useState<AdminTeam | null>(null);

  const load = () =>
    api<AdminTeam[]>("/api/admin/teams")
      .then(setTeams)
      .catch(() => setTeams([]));
  useEffect(() => {
    load();
  }, []);

  async function remove(t: AdminTeam) {
    try {
      await api(`/api/admin/teams/${t.id}`, { method: "DELETE" });
      notify(`Deleted ${t.name}`);
      setDeleting(null);
      load();
      refreshMeta();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't delete the team", "error");
    }
  }

  return (
    <section>
      <div className="row between wrap admin-head">
        <h2>
          <IconUsers /> Teams
        </h2>
        <button className="btn primary" onClick={() => setEditing("new")}>
          <IconPlus /> Add a team
        </button>
      </div>
      <p className="muted small section-note">
        Every person belongs to one team, and every document is owned by one. Teams can also come from your Google or
        Microsoft directory (from people's department).
      </p>
      {teams === null ? (
        <Skeleton rows={3} />
      ) : (
        <div className="table-wrap">
          <table className="people">
            <thead>
              <tr>
                <th scope="col">Team</th>
                <th scope="col">People</th>
                <th scope="col">Documents</th>
                <th scope="col">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {teams.map((t) => (
                <tr key={t.id}>
                  <td>
                    <TeamChip team={t.name} />
                    {t.description && <span className="muted small person-sub">{t.description}</span>}
                  </td>
                  <td>{t.members}</td>
                  <td>{t.documents}</td>
                  <td className="row-actions">
                    <button className="btn small ghost" onClick={() => setEditing(t)} aria-label={`Edit ${t.name}`}>
                      Edit
                    </button>
                    <button
                      className="btn small ghost danger-text"
                      onClick={() => setDeleting(t)}
                      disabled={t.members > 0 || t.documents > 0}
                      title={t.members > 0 || t.documents > 0 ? "Move its people and documents to another team first" : undefined}
                      aria-label={`Delete ${t.name}`}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editing && (
        <TeamDialog
          team={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onDone={() => {
            setEditing(null);
            load();
            refreshMeta();
          }}
        />
      )}
      {deleting && (
        <Modal title={`Delete ${deleting.name}?`} onClose={() => setDeleting(null)}>
          <div className="stack">
            <p className="muted">The team has no people or documents, so nothing else changes.</p>
            <div className="row end">
              <button className="btn ghost" onClick={() => setDeleting(null)}>
                Keep it
              </button>
              <button className="btn danger" onClick={() => remove(deleting)}>
                Delete team
              </button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}

function TeamDialog({ team, onClose, onDone }: { team: AdminTeam | null; onClose: () => void; onDone: () => void }) {
  const { notify } = useApp();
  const [name, setName] = useState(team?.name ?? "");
  const [description, setDescription] = useState(team?.description ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(team ? `/api/admin/teams/${team.id}` : "/api/admin/teams", {
        method: team ? "PATCH" : "POST",
        json: { name: name.trim(), description: description.trim() },
      });
      notify(team ? `Saved ${name.trim()}` : `Added ${name.trim()}`);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the team");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={team ? `Edit ${team.name}` : "Add a team"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} />
        </label>
        <label className="field">
          <span>What the team does</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={255} />
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
          <button className="btn primary" disabled={busy || !name.trim()}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
