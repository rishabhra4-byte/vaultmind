import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../api";
import { Empty, Modal, Skeleton, timeAgo } from "../../components/bits";
import { IconCloud, IconPlus, IconRefresh, IconTrash } from "../../components/icons";
import { useApp } from "../../state";
import type { Classification, Source } from "../../types";

type Kind = Source["kind"];

interface SourcesResponse {
  sources: Source[];
  available: Record<Kind, boolean>;
  folder_roots: string[];
}

interface Rule {
  prefix: string;
  team: string;
  classification: Classification;
}

const KIND_LABEL: Record<Kind, string> = {
  folder: "Shared folder",
  google_drive: "Google Drive",
  microsoft_365: "Microsoft 365 (OneDrive, SharePoint)",
};
const STATUS: Record<string, string> = { ok: "Synced", error: "Failing", syncing: "Syncing…", idle: "Not synced yet" };
const STAT_LABELS: [string, string][] = [
  ["files", "files"],
  ["added", "added"],
  ["updated", "updated"],
  ["removed", "removed"],
  ["skipped", "skipped"],
];

export default function Sources() {
  const { notify } = useApp();
  const [data, setData] = useState<SourcesResponse | null>(null);
  const [editing, setEditing] = useState<Source | "new" | null>(null);
  const [removing, setRemoving] = useState<Source | null>(null);

  const load = useCallback(
    () =>
      api<SourcesResponse>("/api/admin/sources")
        .then(setData)
        .catch(() =>
          setData({ sources: [], available: { folder: false, google_drive: false, microsoft_365: false }, folder_roots: [] }),
        ),
    [],
  );
  useEffect(() => {
    load();
  }, [load]);

  // While anything is syncing, check back every few seconds.
  const syncing = data?.sources.some((s) => s.status === "syncing");
  useEffect(() => {
    if (!syncing) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [syncing, load]);

  async function patch(s: Source, change: Record<string, unknown>, message: string) {
    try {
      await api(`/api/admin/sources/${s.id}`, { method: "PATCH", json: change });
      notify(message);
      load();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't save that", "error");
    }
  }

  async function syncNow(s: Source) {
    try {
      await api(`/api/admin/sources/${s.id}/sync`, { method: "POST" });
      notify(`Syncing ${s.name}`);
      load();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't start the sync", "error");
    }
  }

  async function remove(s: Source) {
    try {
      await api(`/api/admin/sources/${s.id}`, { method: "DELETE" });
      notify(`Disconnected ${s.name}`);
      setRemoving(null);
      load();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't disconnect it", "error");
    }
  }

  return (
    <section>
      <div className="row between wrap admin-head">
        <h2>
          <IconCloud /> Sources
        </h2>
        <button className="btn primary" onClick={() => setEditing("new")}>
          <IconPlus /> Connect a source
        </button>
      </div>
      <p className="muted small section-note">
        Where company files live. VaultMind reads them on a schedule; files are never changed in the source. Google and Microsoft
        files can keep exactly the sharing they have there.
      </p>
      {data === null ? (
        <Skeleton rows={2} />
      ) : data.sources.length === 0 ? (
        <Empty title="No sources connected yet." icon={<IconCloud />}>
          Connect a shared folder, Google Drive or Microsoft 365.
        </Empty>
      ) : (
        <ul className="card-list">
          {data.sources.map((s) => {
            const errors = Array.isArray(s.stats.errors) ? (s.stats.errors as string[]) : [];
            return (
              <li key={s.id} className="list-card source-card">
                <div className="list-card-main">
                  <div className="row wrap">
                    <strong>{s.name}</strong>
                    <span className="pill">{s.label}</span>
                    <span className={`status status-${s.enabled ? s.status : "paused"}`}>
                      {s.enabled ? (STATUS[s.status] ?? s.status) : "Paused"}
                    </span>
                  </div>
                  <p className="muted small">
                    {s.document_count} documents · owned by {s.owner_team} ·{" "}
                    {s.permission_mode === "mirror" ? "keeps the sharing from the source" : "access set by folder rules"}
                    {s.sync_directory && " · syncs people and groups"} · every{" "}
                    {s.sync_interval_minutes >= 60 ? `${s.sync_interval_minutes / 60} h` : `${s.sync_interval_minutes} min`}
                    {s.last_synced_at && ` · last synced ${timeAgo(s.last_synced_at)}`}
                  </p>
                  {Object.keys(s.stats).length > 0 && (
                    <p className="small">
                      Last sync:{" "}
                      {STAT_LABELS.filter(([k]) => typeof s.stats[k] === "number" && (s.stats[k] as number) > 0)
                        .map(([k, label]) => `${s.stats[k]} ${label}`)
                        .join(", ") || "no changes"}
                    </p>
                  )}
                  {s.last_error && <p className="error small">{s.last_error}</p>}
                  {errors.length > 0 && (
                    <details className="small">
                      <summary>
                        {errors.length} file{errors.length === 1 ? "" : "s"} couldn't be read
                      </summary>
                      <ul className="plain">
                        {errors.map((e) => (
                          <li key={e}>{e}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                  <div className="row wrap source-switches">
                    <label className="check">
                      <span className="switch">
                        <input
                          type="checkbox"
                          checked={s.enabled}
                          onChange={(e) =>
                            patch(s, { enabled: e.target.checked }, e.target.checked ? "Syncing resumed" : "Syncing paused")
                          }
                          aria-label={`Sync ${s.name}`}
                        />
                        <span />
                      </span>
                      Sync
                    </label>
                    <label className="check">
                      <span className="switch">
                        <input
                          type="checkbox"
                          checked={s.ai_enabled}
                          onChange={(e) =>
                            patch(
                              s,
                              { ai_enabled: e.target.checked },
                              e.target.checked ? "The assistant can read these files" : "These files are kept away from the AI",
                            )
                          }
                          aria-label={`Let the assistant read files from ${s.name}`}
                        />
                        <span />
                      </span>
                      Assistant can read these files
                    </label>
                  </div>
                </div>
                <div className="row wrap source-actions">
                  <button className="btn small" onClick={() => syncNow(s)} disabled={s.status === "syncing" || !s.enabled}>
                    <IconRefresh width={14} height={14} /> Sync now
                  </button>
                  <button className="btn small" onClick={() => setEditing(s)}>
                    Settings
                  </button>
                  <button
                    className="btn small ghost danger-text"
                    onClick={() => setRemoving(s)}
                    aria-label={`Disconnect ${s.name}`}
                  >
                    <IconTrash width={14} height={14} />
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {editing && data && (
        <SourceDialog
          source={editing === "new" ? null : editing}
          available={data.available}
          roots={data.folder_roots}
          onClose={() => setEditing(null)}
          onDone={() => {
            setEditing(null);
            load();
          }}
        />
      )}
      {removing && (
        <Modal title={`Disconnect ${removing.name}?`} onClose={() => setRemoving(null)}>
          <div className="stack">
            <p className="muted">
              Its {removing.document_count} documents are removed from VaultMind. The files themselves stay where they are in{" "}
              {removing.label}.
            </p>
            <div className="row end">
              <button className="btn ghost" onClick={() => setRemoving(null)}>
                Keep it
              </button>
              <button className="btn danger" onClick={() => remove(removing)}>
                Disconnect
              </button>
            </div>
          </div>
        </Modal>
      )}
    </section>
  );
}

function SourceDialog({
  source,
  available,
  roots,
  onClose,
  onDone,
}: {
  source: Source | null;
  available: Record<Kind, boolean>;
  roots: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const { meta, notify } = useApp();
  const config = (source?.config ?? {}) as Record<string, unknown>;
  const firstAvailable = (Object.keys(available) as Kind[]).find((k) => available[k]) ?? "folder";
  const [kind, setKind] = useState<Kind>(source?.kind ?? firstAvailable);
  const [name, setName] = useState(source?.name ?? "");
  const [team, setTeam] = useState(source?.owner_team_id ?? meta?.teams[0]?.id ?? 0);
  const [cls, setCls] = useState<Classification>(source?.classification ?? "internal");
  const [mode, setMode] = useState<"mirror" | "rules">(source?.permission_mode ?? "mirror");
  const [ai, setAi] = useState(source?.ai_enabled ?? true);
  const [directory, setDirectory] = useState(source?.sync_directory ?? false);
  const [interval, setIntervalMinutes] = useState(source?.sync_interval_minutes ?? 60);
  const [path, setPath] = useState(String(config.path ?? roots[0] ?? ""));
  const [folders, setFolders] = useState(((config.folders as string[]) ?? []).join("\n"));
  const [where, setWhere] = useState<"site" | "user" | "drive">(config.user_email ? "user" : config.drive_id ? "drive" : "site");
  const [siteUrl, setSiteUrl] = useState(String(config.site_url ?? ""));
  const [library, setLibrary] = useState(String(config.library ?? "Documents"));
  const [userEmail, setUserEmail] = useState(String(config.user_email ?? ""));
  const [driveId, setDriveId] = useState(String(config.drive_id ?? ""));
  const [folderPath, setFolderPath] = useState(String(config.folder_path ?? ""));
  const [rules, setRules] = useState<Rule[]>(
    ((config.rules as { prefix?: string; team?: string | number; classification?: Classification }[]) ?? []).map((r) => ({
      prefix: r.prefix ?? "",
      team: String(typeof r.team === "number" ? (meta?.teams.find((t) => t.id === r.team)?.name ?? "") : (r.team ?? "")),
      classification: r.classification ?? "internal",
    })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const isFolder = kind === "folder";
  const usesRules = isFolder || mode === "rules";

  function buildConfig(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    if (usesRules && rules.length) out.rules = rules.filter((r) => r.team).map((r) => ({ ...r, prefix: r.prefix.trim() }));
    if (kind === "folder") out.path = path.trim();
    if (kind === "google_drive")
      out.folders = folders
        .split(/[\n,]/)
        .map((f) => f.trim())
        .filter(Boolean);
    if (kind === "microsoft_365") {
      if (where === "site") Object.assign(out, { site_url: siteUrl.trim(), library: library.trim() || "Documents" });
      if (where === "user") out.user_email = userEmail.trim();
      if (where === "drive") out.drive_id = driveId.trim();
      if (folderPath.trim()) out.folder_path = folderPath.trim();
    }
    return out;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const body = {
      name: name.trim(),
      config: buildConfig(),
      owner_team_id: team,
      classification: cls,
      permission_mode: isFolder ? "rules" : mode,
      ai_enabled: ai,
      sync_directory: isFolder ? false : directory,
      sync_interval_minutes: interval,
    };
    try {
      if (source) await api(`/api/admin/sources/${source.id}`, { method: "PATCH", json: body });
      else await api("/api/admin/sources", { method: "POST", json: { ...body, kind } });
      notify(source ? "Saved. Changes apply at the next sync." : `Connected ${name.trim()}. The first sync has started.`);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't save the source");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={source ? `Settings for ${source.name}` : "Connect a source"} onClose={onClose}>
      <form className="stack source-form" onSubmit={submit}>
        {!source && (
          <fieldset className="field">
            <legend>Where the files are</legend>
            <div className="kind-picker">
              {(Object.keys(KIND_LABEL) as Kind[]).map((k) => (
                <label key={k} className={`kind-option ${kind === k ? "on" : ""} ${available[k] ? "" : "off"}`}>
                  <input type="radio" name="kind" checked={kind === k} disabled={!available[k]} onChange={() => setKind(k)} />
                  <strong>{KIND_LABEL[k]}</strong>
                  {!available[k] && <span className="muted small">Not set up on this server yet. See the setup guide.</span>}
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <label className="field">
          <span>Name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={120}
            placeholder="e.g. Company shared drive"
          />
        </label>

        {kind === "folder" && (
          <label className="field">
            <span>Folder on the server</span>
            <input value={path} onChange={(e) => setPath(e.target.value)} required />
            <small className="muted">
              {roots.length ? `Must be inside: ${roots.join(", ")}` : "No folders are allowed yet (VAULTMIND_FOLDER_ROOTS)."}
            </small>
          </label>
        )}
        {kind === "google_drive" && (
          <label className="field">
            <span>Drive folders</span>
            <textarea
              value={folders}
              onChange={(e) => setFolders(e.target.value)}
              rows={3}
              required
              placeholder="Paste folder links or IDs, one per line"
            />
            <small className="muted">Shared drives work too. Subfolders are included.</small>
          </label>
        )}
        {kind === "microsoft_365" && (
          <>
            <fieldset className="field">
              <legend>Which files</legend>
              <div className="row wrap">
                {(
                  [
                    ["site", "A SharePoint site"],
                    ["user", "Someone's OneDrive"],
                    ["drive", "A drive ID"],
                  ] as const
                ).map(([v, label]) => (
                  <label key={v} className="check">
                    <input type="radio" name="where" checked={where === v} onChange={() => setWhere(v)} />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
            {where === "site" && (
              <div className="grid-2">
                <label className="field">
                  <span>Site address</span>
                  <input
                    value={siteUrl}
                    onChange={(e) => setSiteUrl(e.target.value)}
                    required
                    placeholder="https://contoso.sharepoint.com/sites/HR"
                  />
                </label>
                <label className="field">
                  <span>Library</span>
                  <input value={library} onChange={(e) => setLibrary(e.target.value)} />
                </label>
              </div>
            )}
            {where === "user" && (
              <label className="field">
                <span>Their email</span>
                <input type="email" value={userEmail} onChange={(e) => setUserEmail(e.target.value)} required />
              </label>
            )}
            {where === "drive" && (
              <label className="field">
                <span>Drive ID</span>
                <input value={driveId} onChange={(e) => setDriveId(e.target.value)} required />
              </label>
            )}
            <label className="field">
              <span>Only this folder (optional)</span>
              <input value={folderPath} onChange={(e) => setFolderPath(e.target.value)} placeholder="e.g. Policies/2026" />
            </label>
          </>
        )}

        {!isFolder && (
          <fieldset className="field">
            <legend>Who can open the files in VaultMind</legend>
            <label className="check">
              <input type="radio" name="mode" checked={mode === "mirror"} onChange={() => setMode("mirror")} />
              Exactly who they're shared with in {kind === "google_drive" ? "Google Drive" : "Microsoft 365"}
            </label>
            <label className="check">
              <input type="radio" name="mode" checked={mode === "rules"} onChange={() => setMode("rules")} />
              VaultMind's team and classification rules
            </label>
          </fieldset>
        )}

        <div className="grid-2">
          <label className="field">
            <span>Owning team</span>
            <select value={team} onChange={(e) => setTeam(Number(e.target.value))}>
              {meta?.teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{usesRules ? "Default classification" : "Classification label"}</span>
            <select value={cls} onChange={(e) => setCls(e.target.value as Classification)}>
              {meta?.classifications.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        {usesRules && (
          <fieldset className="field">
            <legend>Folder rules</legend>
            <small className="muted">
              Files in a folder get that folder's team and classification; the longest matching folder wins. The scanner can still
              raise a classification when it finds sensitive data.
            </small>
            {rules.map((r, i) => (
              <div key={i} className="rule-row">
                <input
                  value={r.prefix}
                  onChange={(e) => setRules((rs) => rs.map((x, j) => (j === i ? { ...x, prefix: e.target.value } : x)))}
                  placeholder="Folder, e.g. HR/Payroll"
                  aria-label={`Folder for rule ${i + 1}`}
                />
                <select
                  value={r.team}
                  onChange={(e) => setRules((rs) => rs.map((x, j) => (j === i ? { ...x, team: e.target.value } : x)))}
                  aria-label={`Team for rule ${i + 1}`}
                >
                  <option value="">Team…</option>
                  {meta?.teams.map((t) => (
                    <option key={t.id} value={t.name}>
                      {t.name}
                    </option>
                  ))}
                </select>
                <select
                  value={r.classification}
                  onChange={(e) =>
                    setRules((rs) => rs.map((x, j) => (j === i ? { ...x, classification: e.target.value as Classification } : x)))
                  }
                  aria-label={`Classification for rule ${i + 1}`}
                >
                  {meta?.classifications.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => setRules((rs) => rs.filter((_, j) => j !== i))}
                  aria-label={`Remove rule ${i + 1}`}
                >
                  ×
                </button>
              </div>
            ))}
            <button
              type="button"
              className="btn small"
              onClick={() => setRules((rs) => [...rs, { prefix: "", team: "", classification: "internal" }])}
            >
              <IconPlus width={14} height={14} /> Add a rule
            </button>
          </fieldset>
        )}

        <div className="grid-2">
          <label className="field">
            <span>Sync every</span>
            <select value={interval} onChange={(e) => setIntervalMinutes(Number(e.target.value))}>
              {[15, 30, 60, 180, 360, 1440].map((m) => (
                <option key={m} value={m}>
                  {m >= 60 ? `${m / 60} hour${m > 60 ? "s" : ""}` : `${m} minutes`}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="check">
          <input type="checkbox" checked={ai} onChange={(e) => setAi(e.target.checked)} />
          Let the assistant read these files (only for people who can open them)
        </label>
        {!isFolder && (
          <label className="check">
            <input type="checkbox" checked={directory} onChange={(e) => setDirectory(e.target.checked)} />
            Also sync people, teams and groups from the directory
          </label>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={busy || !name.trim() || (!source && !available[kind])}>
            {busy ? "Saving…" : source ? "Save" : "Connect and sync"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
