import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { Avatar, Empty, Skeleton, timeAgo } from "../../components/bits";
import { IconActivity } from "../../components/icons";
import type { AuditEvent } from "../../types";

const ACTIONS: Record<string, string> = {
  "auth.login": "signed in",
  "document.view": "opened",
  "document.view_denied": "was refused",
  "document.download": "downloaded",
  "document.download_denied": "was refused a download of",
  "document.upload": "uploaded",
  "document.update": "changed",
  "document.delete": "deleted",
  "document.new_version": "uploaded a new version of",
  "assistant.query": "asked the assistant",
  "assistant.action": "confirmed an assistant action",
  "access.request": "requested access to",
  "access.approve": "approved access to",
  "access.deny": "denied access to",
  "access.cancel": "cancelled a request for",
  "access.revoke": "revoked access to",
  "access.grant": "gave someone access to",
  "user.create": "added a person",
  "user.update": "updated a person",
  "user.import": "imported people",
  "user.preferences": "changed their notification settings",
  "team.create": "added a team",
  "team.update": "renamed a team",
  "team.delete": "deleted a team",
  "group.create": "created a group",
  "group.update": "changed a group",
  "group.delete": "deleted a group",
  "source.create": "connected a source",
  "source.update": "changed a source",
  "source.delete": "disconnected a source",
  "source.sync": "synced a source",
  "glossary.add": "added a glossary term",
  "glossary.update": "changed a glossary term",
  "glossary.delete": "deleted a glossary term",
};

const FILTERS = [
  ["", "Everything"],
  ["document.view_denied", "Refused access"],
  ["assistant", "Assistant"],
  ["access", "Requests and grants"],
  ["document.upload", "Uploads"],
  ["source", "Sources"],
  ["user", "People"],
];

export default function Activity() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    setEvents(null);
    api<AuditEvent[]>(`/api/admin/audit?limit=150${filter ? `&action=${encodeURIComponent(filter)}` : ""}`)
      .then(setEvents)
      .catch(() => setEvents([]));
  }, [filter]);

  return (
    <section>
      <div className="row between wrap admin-head">
        <h2>
          <IconActivity /> Activity
        </h2>
        <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter activity">
          {FILTERS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </div>
      {events === null ? (
        <Skeleton rows={4} />
      ) : events.length === 0 ? (
        <Empty title="Nothing here yet." icon={<IconActivity />}>
          Activity matching this filter will appear as people use VaultMind.
        </Empty>
      ) : (
        <ul className="audit">
          {events.map((e) => (
            <li key={e.id} className={e.action.endsWith("_denied") ? "denied" : ""}>
              <Avatar name={e.actor ?? "System"} team={e.actor ?? "System"} size={30} />
              <span>
                <strong>{e.actor ?? "System"}</strong> {ACTIONS[e.action] ?? e.action}{" "}
                {e.target_type === "document" && e.target_id && typeof e.detail.title === "string" ? (
                  <Link to={`/library/${e.target_id}`}>{e.detail.title}</Link>
                ) : typeof e.detail.name === "string" ? (
                  <span>{e.detail.name}</span>
                ) : null}
                {e.action === "assistant.query" && typeof e.detail.question === "string" && (
                  <span className="muted"> “{e.detail.question}”</span>
                )}
                {e.action === "assistant.query" && e.detail.channel === "slack" && <span className="muted"> in Slack</span>}
                {typeof e.detail.reason === "string" && <span className="muted"> ({e.detail.reason} rule)</span>}
              </span>
              <time dateTime={e.created_at} title={new Date(e.created_at).toLocaleString()}>
                {timeAgo(e.created_at)}
              </time>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
