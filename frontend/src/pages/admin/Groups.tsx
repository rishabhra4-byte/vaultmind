import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../../api";
import { Avatar, Empty, Modal, Skeleton } from "../../components/bits";
import { IconPlus, IconSearch, IconUsers } from "../../components/icons";
import { useApp } from "../../state";
import type { AdminUser, Group } from "../../types";

const FROM: Record<string, string> = { google: "Google Workspace", microsoft: "Microsoft 365" };

export default function Groups() {
  const { notify } = useApp();
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [people, setPeople] = useState<AdminUser[]>([]);
  const [editing, setEditing] = useState<Group | "new" | null>(null);
  const [deleting, setDeleting] = useState<Group | null>(null);

  const load = () =>
    api<Group[]>("/api/admin/groups")
      .then(setGroups)
      .catch(() => setGroups([]));
  useEffect(() => {
    load();
    api<AdminUser[]>("/api/admin/users")
      .then(setPeople)
      .catch(() => {});
  }, []);

  async function remove(g: Group) {
    try {
      await api(`/api/admin/groups/${g.id}`, { method: "DELETE" });
      notify(`Deleted ${g.name}`);
      setDeleting(null);
      load();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't delete the group", "error");
    }
  }

  const byId = new Map(people.map((p) => [p.id, p]));
  return (
    <section>
      <div className="row between wrap section-head">
        <h2>
          <IconUsers /> Groups
        </h2>
        <button className="btn primary" onClick={() => setEditing("new")}>
          <IconPlus /> New group
        </button>
      </div>
      <p className="muted small section-note">
        Groups cut across teams: share a document with a group and its members can open it (still subject to the
        document's minimum position). Groups from Google or Microsoft are kept in sync and edited there.
      </p>
      {groups === null ? (
        <Skeleton rows={3} />
      ) : groups.length === 0 ? (
        <Empty title="No groups yet." icon={<IconUsers />}>
          Create one for a project or a committee, then share documents with it.
        </Empty>
      ) : (
        <ul className="card-list">
          {groups.map((g) => (
            <li key={g.id} className="list-card">
              <div className="list-card-main">
                <strong>{g.name}</strong>
                <span className="pill">{g.provider === "manual" ? "Made here" : `From ${FROM[g.provider] ?? g.provider}`}</span>
                {g.description && <p className="muted small">{g.description}</p>}
                <div className="avatars" aria-label={`${g.members} member${g.members === 1 ? "" : "s"}`}>
                  {(g.member_ids ?? []).slice(0, 8).map((id) => {
                    const p = byId.get(id);
                    return p ? <Avatar key={id} name={p.full_name} team={p.team_name} size={26} /> : null;
                  })}
                  <span className="muted small">
                    {g.members} member{g.members === 1 ? "" : "s"}
                  </span>
                </div>
              </div>
              {g.provider === "manual" && (
                <div className="row">
                  <button className="btn small" onClick={() => setEditing(g)} aria-label={`Edit ${g.name}`}>
                    Edit
                  </button>
                  <button className="btn small ghost danger-text" onClick={() => setDeleting(g)} aria-label={`Delete ${g.name}`}>
                    Delete
                  </button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {editing && (
        <GroupDialog
          group={editing === "new" ? null : editing}
          people={people}
          onClose={() => setEditing(null)}
          onDone={() => {
            setEditing(null);
            load();
          }}
        />
      )}
      {deleting && (
        <Modal title={`Delete ${deleting.name}?`} onClose={() => setDeleting(null)}>
          <div className="stack">
            <p className="muted">
              Documents shared with this group stop being shared with it. Its members keep any other access they have.
            </p>
            <div className="row end">
              <button className="btn ghost" onClick={() => setDeleting(null)}>
                Keep it
              </button>
              <button className="btn danger" onClick={() => remove(deleting)}>
                Delete group
              </button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}

function GroupDialog({
  group,
  people,
  onClose,
  onDone,
}: {
  group: Group | null;
  people: AdminUser[];
  onClose: () => void;
  onDone: () => void;
}) {
  const { notify } = useApp();
  const [name, setName] = useState(group?.name ?? "");
  const [description, setDescription] = useState(group?.description ?? "");
  const [members, setMembers] = useState<number[]>(group?.member_ids ?? []);
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return people.filter((p) => p.is_active && (!q || p.full_name.toLowerCase().includes(q) || p.team_name.toLowerCase().includes(q)));
  }, [people, filter]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(group ? `/api/admin/groups/${group.id}` : "/api/admin/groups", {
        method: group ? "PATCH" : "POST",
        json: { name: name.trim(), description: description.trim(), member_ids: members },
      });
      notify(group ? `Saved ${name.trim()}` : `Created ${name.trim()}`);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the group");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={group ? `Edit ${group.name}` : "New group"} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label className="field">
          <span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
        </label>
        <label className="field">
          <span>What it's for</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} maxLength={255} />
        </label>
        <fieldset className="field">
          <legend>
            Members ({members.length})
          </legend>
          <label className="search-box">
            <IconSearch />
            <input
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Find people by name or team"
              aria-label="Find people"
            />
          </label>
          <ul className="member-picker">
            {shown.map((p) => (
              <li key={p.id}>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={members.includes(p.id)}
                    onChange={(e) => setMembers((m) => (e.target.checked ? [...m, p.id] : m.filter((x) => x !== p.id)))}
                  />
                  <Avatar name={p.full_name} team={p.team_name} size={24} />
                  {p.full_name} <span className="muted small">{p.team_name}</span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
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
