import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../api";
import { Skeleton, timeAgo } from "../../components/bits";
import { IconFile, IconFolder, IconSearch, IconTreemap } from "../../components/icons";
import {
  AGE_BUCKETS,
  ageBucket,
  amount,
  buildTree,
  bytes,
  CLASS_ORDER,
  colorClass,
  docsUnder,
  KIND_LABELS,
  kindColors,
  pathTo,
  share,
  sizeOf,
  walk,
  type ColorBy,
  type GroupBy,
  type SizeBy,
  type TNode,
} from "../../components/libraryTree";
import { Treemap } from "../../components/Treemap";
import { useApp } from "../../state";
import type { CleanupItem, LibraryDoc, LibraryMapData } from "../../types";

type Tab = "folder" | "largest" | "duplicates" | "cleanup";

const SIZE_LABEL: Record<SizeBy, string> = { bytes: "File size", passages: "Passages", use: "Use (opens and answers)" };

const CLEANUP: { kind: CleanupItem["kind"]; title: string; note: string }[] = [
  {
    kind: "same_file",
    title: "Extra copies of the same file",
    note: "Files are stored once, so a copy takes no extra room. The cost is in answers: search shows both, and when one is updated, answers can quote the other.",
  },
  {
    kind: "same_text",
    title: "Almost the same text",
    note: "An old copy and a new one, or a PDF and the file it came from. Answers can quote either, so keep the right one.",
  },
  { kind: "old_but_used", title: "Old, but people still rely on it", note: "Not updated for a year or more, yet opened or used in answers recently." },
  { kind: "unused", title: "Not used", note: "Nobody opened these or got them in an answer during the period. Older than the period." },
];

const num = (v: number, by: SizeBy) => (by === "bytes" ? bytes(v) : v.toLocaleString());
const date = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
const cap = (s: string) => s[0].toUpperCase() + s.slice(1);
const times = (n: number) => (n === 1 ? "once" : n === 2 ? "twice" : `${n} times`);
function usedFor(d: LibraryDoc, days: number): string {
  const uses = [d.opens ? `opened ${times(d.opens)}` : "", d.cited ? `in ${d.cited === 1 ? "an answer" : `${d.cited} answers`}` : ""];
  const said = uses.filter(Boolean).join(", ");
  return said ? `${said} in the last ${days} days` : `not opened or in an answer in the last ${days} days`;
}

/** The library map, after disk-usage tools like WinDirStat: a treemap of every document, file
 *  types with matching colours, the largest files, duplicates and what to clean up. */
export default function LibraryMap() {
  const { notify } = useApp();
  const [data, setData] = useState<LibraryMapData | null>(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(90);
  const [groupBy, setGroupBy] = useState<GroupBy>("team");
  const [sizeBy, setSizeBy] = useState<SizeBy>("bytes");
  const [colorBy, setColorBy] = useState<ColorBy>("kind");
  const [zoom, setZoom] = useState<string[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [focus, setFocus] = useState<{ key: string; label: string; ids: Set<number> } | null>(null);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<Tab>("folder");
  const tabsRef = useRef<HTMLDivElement>(null);

  const load = (d = days) => {
    setLoading(true);
    return api<LibraryMapData>(`/api/admin/library?days=${d}`)
      .then(setData)
      .catch(() => notify("Couldn't load the library map", "error"))
      .finally(() => setLoading(false));
  };
  useEffect(() => {
    load(days);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

  const tree = useMemo(() => (data ? buildTree(data, groupBy, sizeBy) : null), [data, groupBy, sizeBy]);
  const trail = useMemo(() => (tree ? walk(tree, zoom) : []), [tree, zoom]);
  const here = trail[trail.length - 1];
  const kinds = useMemo(() => (data ? kindColors(data) : new Map()), [data]);
  const byId = useMemo(() => new Map((data?.documents ?? []).map((d) => [d.id, d])), [data]);
  const scope = useMemo(() => (here ? docsUnder(here) : []), [here]);
  const teams = useMemo(() => new Map((data?.teams ?? []).map((t) => [t.id, t.name])), [data]);
  const sources = useMemo(() => new Map((data?.sources ?? []).map((s) => [s.id, s.name])), [data]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    return new Set(scope.filter((d) => d.title.toLowerCase().includes(q) || d.folder.toLowerCase().includes(q)).map((d) => d.id));
  }, [query, scope]);
  const highlight = useMemo(() => {
    if (!focus && !matches) return null;
    if (focus && matches) return new Set([...focus.ids].filter((id) => matches.has(id)));
    return focus?.ids ?? matches;
  }, [focus, matches]);

  if (!data || !tree || !here) {
    return (
      <section>
        <h2>
          <IconTreemap /> Library map
        </h2>
        <Skeleton rows={4} />
      </section>
    );
  }

  const where = (d: LibraryDoc) =>
    [teams.get(d.team_id), d.source_id !== null ? sources.get(d.source_id) : "Uploaded here", d.folder].filter(Boolean).join(" › ");
  const chosen = selected !== null ? byId.get(selected) : undefined;

  function change<T>(set: (v: T) => void, v: T) {
    set(v);
    setZoom([]);
    setFocus(null);
  }

  function showOnMap(ids: number[], label: string) {
    setZoom([]);
    setFocus({ key: `ids:${ids.join(",")}`, label, ids: new Set(ids) });
    if (ids.length === 1) setSelected(ids[0]);
    document.querySelector(".lib-stage")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function select(id: number) {
    setSelected(id === selected ? null : id);
  }

  function selectAndReveal(id: number) {
    const path = tree ? pathTo(tree, id) : null;
    if (path) setZoom(path);
    setFocus(null);
    setSelected(id);
    document.querySelector(".lib-stage")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // The colour list (WinDirStat's extension list): one row per colour in this view.
  const legend = (() => {
    const rows = new Map<string, { key: string; tone: string; label: string; ids: number[]; bytes: number; value: number }>();
    const add = (key: string, tone: string, label: string, d: LibraryDoc) => {
      const row = rows.get(key) ?? { key, tone, label, ids: [], bytes: 0, value: 0 };
      row.ids.push(d.id);
      row.bytes += d.bytes;
      row.value += sizeOf(d, sizeBy);
      rows.set(key, row);
    };
    for (const d of scope) {
      const tone = colorClass(d, colorBy, kinds);
      if (colorBy === "kind") add(`kind:${d.kind}`, tone, KIND_LABELS[d.kind], d);
      else if (colorBy === "classification") add(`cls:${d.classification}`, tone, cap(d.classification), d);
      else add(`age:${ageBucket(d.updated_at)}`, tone, AGE_BUCKETS[ageBucket(d.updated_at)].label, d);
    }
    const order = (key: string) =>
      colorBy === "classification"
        ? CLASS_ORDER.indexOf(key.slice(4) as never)
        : colorBy === "age"
          ? Number(key.slice(4))
          : -(rows.get(key)?.value ?? 0);
    return [...rows.values()].sort((a, b) => order(a.key) - order(b.key));
  })();
  const legendTotal = legend.reduce((s, r) => s + r.value, 0);

  const largest = [...scope].sort((a, b) => b.bytes - a.bytes).slice(0, 25);
  const tabs: [Tab, string][] = [
    ["folder", here === tree ? "Top level" : `In ${here.name}`],
    ["largest", "Largest"],
    ["duplicates", `Duplicates${data.duplicates.length ? ` (${data.duplicates.length})` : ""}`],
    ["cleanup", `Clean up${data.cleanup.length ? ` (${data.cleanup.length})` : ""}`],
  ];

  function onTabKey(e: KeyboardEvent) {
    const i = tabs.findIndex(([t]) => t === tab);
    const next = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : -1;
    if (next < 0) return;
    e.preventDefault();
    setTab(tabs[next][0]);
    tabsRef.current?.querySelectorAll<HTMLButtonElement>("[role=tab]")[next]?.focus();
  }

  async function remove(item: CleanupItem) {
    try {
      await api(`/api/documents/${item.doc_id}`, { method: "DELETE" });
      notify("Deleted that copy");
      if (selected === item.doc_id) setSelected(null);
      setFocus(null);
      load();
    } catch (err) {
      notify(err instanceof ApiError ? err.message : "Couldn't delete it", "error");
    }
  }

  return (
    <section className="lib-map">
      <div className="row between wrap admin-head">
        <h2>
          <IconTreemap /> Library map
        </h2>
        <span className="muted small">Updated {timeAgo(data.generated_at)}</span>
      </div>
      <p className="muted small section-note">
        Every document as a rectangle, sized by how much room it takes and grouped by team, source and folder, like a disk-usage
        map. Click a group's name to zoom in, a document to see its details. Below: the largest files, duplicates, and what to
        clean up.
      </p>

      <dl className="figures lib-figures">
        <div className="figure">
          <dt>Documents</dt>
          <dd>{data.totals.documents.toLocaleString()}</dd>
        </div>
        <div className="figure">
          <dt>Total size</dt>
          <dd>{bytes(data.totals.bytes)}</dd>
        </div>
        <div className="figure">
          <dt>Passages searched</dt>
          <dd>{data.totals.passages.toLocaleString()}</dd>
        </div>
        <div className="figure">
          <dt>Duplicate sets</dt>
          <dd>{data.duplicates.length}</dd>
        </div>
        <div className="figure">
          <dt>To clean up</dt>
          <dd>{data.cleanup.length}</dd>
        </div>
      </dl>
      {data.truncated && (
        <p className="callout warn" role="status">
          The library has more documents than the map draws at once; it shows the {data.documents.length.toLocaleString()} largest.
          Totals, duplicates and clean-up cover everything.
        </p>
      )}

      <div className="lib-controls" role="group" aria-label="Map settings">
        <label className="field compact">
          <span>Group by</span>
          <select value={groupBy} onChange={(e) => change(setGroupBy, e.target.value as GroupBy)}>
            <option value="team">Team, then source and folder</option>
            <option value="source">Source and folder</option>
          </select>
        </label>
        <label className="field compact">
          <span>Size by</span>
          <select value={sizeBy} onChange={(e) => setSizeBy(e.target.value as SizeBy)}>
            {(Object.keys(SIZE_LABEL) as SizeBy[]).map((s) => (
              <option key={s} value={s}>
                {SIZE_LABEL[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="field compact">
          <span>Colour by</span>
          <select
            value={colorBy}
            onChange={(e) => {
              setColorBy(e.target.value as ColorBy);
              setFocus(null);
            }}
          >
            <option value="kind">File type</option>
            <option value="classification">Classification</option>
            <option value="age">Last updated</option>
          </select>
        </label>
        <label className="field compact">
          <span>Use counted over</span>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[30, 90, 180, 365].map((d) => (
              <option key={d} value={d}>
                Last {d} days
              </option>
            ))}
          </select>
        </label>
        <label className="field compact grow lib-find">
          <span>Find</span>
          <span className="input-icon">
            <IconSearch />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Title or folder"
              onKeyDown={(e) => {
                if (e.key === "Enter" && matches && matches.size) {
                  const first = scope.find((d) => matches.has(d.id));
                  if (first) setSelected(first.id);
                }
              }}
            />
          </span>
        </label>
      </div>

      <nav className="lib-crumbs" aria-label="Where you are in the map">
        <ol>
          {trail.map((n, i) => (
            <li key={n.key}>
              {i === trail.length - 1 ? (
                <span aria-current="location">{n.name}</span>
              ) : (
                <button className="link-btn lib-link" onClick={() => setZoom(zoom.slice(0, i))}>
                  {n.name}
                </button>
              )}
            </li>
          ))}
        </ol>
        <span className="muted small">
          {here.docs} {here.docs === 1 ? "document" : "documents"} · {amount(here.value, sizeBy)}
          {matches && ` · ${matches.size} found`}
        </span>
      </nav>

      <div className={`lib-stage${loading ? " is-loading" : ""}`}>
        <Treemap
          root={here}
          sizeBy={sizeBy}
          colorBy={colorBy}
          kinds={kinds}
          highlight={highlight}
          selected={selected}
          onSelect={select}
          onZoom={(path) => {
            setZoom([...zoom, ...path]);
            setSelected(null);
          }}
          label={`Treemap of ${here.name}: ${here.docs} documents sized by ${SIZE_LABEL[sizeBy].toLowerCase()}. The same figures are in the list below.`}
        />
        <aside className="lib-legend" aria-label="Colours">
          <h3>{colorBy === "kind" ? "File types" : colorBy === "classification" ? "Classification" : "Last updated"}</h3>
          <ul>
            {legend.map((r) => {
              const on = focus?.key === r.key;
              return (
                <li key={r.key}>
                  <button
                    className="lib-legend-row"
                    aria-pressed={on}
                    onClick={() => setFocus(on ? null : { key: r.key, label: r.label, ids: new Set(r.ids) })}
                  >
                    <span className={`swatch tone-${r.tone}`} aria-hidden />
                    <span className="lib-legend-label">
                      {r.label}
                      <span className="muted small">
                        {r.ids.length} {r.ids.length === 1 ? "document" : "documents"} · {bytes(r.bytes)}
                      </span>
                    </span>
                    <span className="lib-legend-num">{share(r.value, legendTotal)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          {colorBy === "kind" && data.kinds.length > 3 && (
            <p className="muted small">The three largest types have a colour; the rest share grey. Click any type to pick it out.</p>
          )}
          {focus && (
            <p className="lib-focus small">
              Showing <strong>{focus.label}</strong>{" "}
              <button className="link-btn lib-link" onClick={() => setFocus(null)}>
                Show all
              </button>
            </p>
          )}
        </aside>
      </div>

      {chosen ? (
        <div className="lib-detail" role="status">
          <span className={`swatch tone-${colorClass(chosen, colorBy, kinds)}`} aria-hidden />
          <div className="grow">
            <Link to={`/library/${chosen.id}`}>
              <strong>{chosen.title}</strong>
            </Link>
            <div className="muted small">
              {where(chosen)} · {KIND_LABELS[chosen.kind]} · {bytes(chosen.bytes)} · {chosen.passages}{" "}
              {chosen.passages === 1 ? "passage" : "passages"} · {cap(chosen.classification)} · updated {date(chosen.updated_at)} ·{" "}
              {usedFor(chosen, data.days)}
              {chosen.duplicate !== null && " · has a duplicate"}
            </div>
          </div>
          <button className="btn small ghost" onClick={() => setSelected(null)}>
            Clear
          </button>
        </div>
      ) : (
        <p className="lib-detail muted small">Select a document in the map or the lists to see its details here.</p>
      )}

      <div className="lib-tabs" role="tablist" aria-label="Lists" ref={tabsRef} onKeyDown={onTabKey}>
        {tabs.map(([t, label]) => (
          <button
            key={t}
            role="tab"
            id={`lib-tab-${t}`}
            aria-selected={tab === t}
            aria-controls={`lib-panel-${t}`}
            tabIndex={tab === t ? 0 : -1}
            onClick={() => setTab(t)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="lib-panel" role="tabpanel" id={`lib-panel-${tab}`} aria-labelledby={`lib-tab-${tab}`}>
        {tab === "folder" && <FolderList node={here} sizeBy={sizeBy} selected={selected} onZoom={(k) => setZoom([...zoom, k])} onSelect={select} />}
        {tab === "largest" && (
          <div className="table-scroll">
            <table className="lib-table">
              <caption className="muted small">The largest files {here === tree ? "in the library" : `in ${here.name}`}.</caption>
              <thead>
                <tr>
                  <th scope="col">Document</th>
                  <th scope="col">Type</th>
                  <th scope="col" className="num">
                    Size
                  </th>
                  <th scope="col">Where</th>
                  <th scope="col">Updated</th>
                </tr>
              </thead>
              <tbody>
                {largest.map((d) => (
                  <tr key={d.id} className={d.id === selected ? "is-selected" : undefined}>
                    <td>
                      <button className="link-btn lib-link" onClick={() => select(d.id)} aria-pressed={d.id === selected}>
                        {d.title}
                      </button>
                    </td>
                    <td>{KIND_LABELS[d.kind]}</td>
                    <td className="num">{bytes(d.bytes)}</td>
                    <td className="muted">{where(d)}</td>
                    <td className="muted">{date(d.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {tab === "duplicates" && (
          <div className="stack">
            <p className="muted small">
              The same file more than once, and documents whose text is almost the same ({data.totals.compared.toLocaleString()} of{" "}
              {data.totals.documents.toLocaleString()} documents are long enough to compare). {CLEANUP[0].note}
            </p>
            {data.duplicates.length === 0 ? (
              <p className="muted">No duplicates found.</p>
            ) : (
              data.duplicates.map((g, i) => (
                <article key={i} className="panel lib-dup">
                  <div className="row between wrap">
                    <h3>
                      {g.kind === "same_file" ? "The same file" : `Almost the same text (about ${Math.round(g.similarity * 100)}% or more)`},{" "}
                      {g.documents.length} documents
                    </h3>
                    <button className="btn small" onClick={() => showOnMap(g.documents, g.kind === "same_file" ? "one duplicate set" : "one set of near-duplicates")}>
                      Show on map
                    </button>
                  </div>
                  <ul className="plain">
                    {g.documents.map((id) => {
                      const d = byId.get(id);
                      if (!d) return null;
                      return (
                        <li key={id}>
                          <Link to={`/library/${id}`}>{d.title}</Link>{" "}
                          <span className="muted small">
                            {where(d)} · {bytes(d.bytes)} · updated {date(d.updated_at)} · {usedFor(d, data.days)}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </article>
              ))
            )}
          </div>
        )}
        {tab === "cleanup" && <Cleanup data={data} byId={byId} where={where} onShow={selectAndReveal} onDelete={remove} sources={sources} />}
      </div>
    </section>
  );
}

/** The directory list: what's directly inside the current group, largest first. */
function FolderList({
  node,
  sizeBy,
  selected,
  onZoom,
  onSelect,
}: {
  node: TNode;
  sizeBy: SizeBy;
  selected: number | null;
  onZoom: (key: string) => void;
  onSelect: (id: number) => void;
}) {
  const rows = node.children.slice(0, 200);
  return (
    <div className="table-scroll">
      <table className="lib-table">
        <caption className="muted small">
          What's in {node.name}, largest first.
          {node.children.length > rows.length && ` Showing the ${rows.length} largest of ${node.children.length}.`}
        </caption>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col" className="num">
              {SIZE_LABEL[sizeBy].replace(" (opens and answers)", "")}
            </th>
            <th scope="col" className="lib-bar-col">
              Share
            </th>
            <th scope="col" className="num">
              Documents
            </th>
            <th scope="col" className="lib-when">
              Last change
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => {
            const pct = node.value ? (c.value / node.value) * 100 : 0;
            return (
              <tr key={c.key} className={c.doc && c.doc.id === selected ? "is-selected" : undefined}>
                <td>
                  {c.doc ? (
                    <button className="link-btn lib-link lib-name" onClick={() => onSelect(c.doc!.id)} aria-pressed={c.doc.id === selected}>
                      <IconFile /> {c.name}
                    </button>
                  ) : (
                    <button className="link-btn lib-link lib-name" onClick={() => onZoom(c.key)} aria-label={`Open ${c.name}`}>
                      <IconFolder /> {c.name}
                    </button>
                  )}
                </td>
                <td className="num">{num(c.value, sizeBy)}</td>
                <td className="lib-bar-col">
                  <span className="lib-bar" aria-hidden>
                    <span style={{ width: `${pct}%` }} />
                  </span>
                  <span className="muted small">{share(c.value, node.value)}</span>
                </td>
                <td className="num">{c.docs}</td>
                <td className="muted lib-when">{c.updated ? date(c.updated) : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Cleanup({
  data,
  byId,
  where,
  sources,
  onShow,
  onDelete,
}: {
  data: LibraryMapData;
  byId: Map<number, LibraryDoc>;
  where: (d: LibraryDoc) => string;
  sources: Map<number, string>;
  onShow: (id: number) => void;
  onDelete: (item: CleanupItem) => void;
}) {
  const [all, setAll] = useState<Record<string, boolean>>({});
  const [confirming, setConfirming] = useState<number | null>(null);
  if (data.cleanup.length === 0) return <p className="muted">Nothing to clean up.</p>;
  return (
    <div className="stack">
      {CLEANUP.map((section) => {
        const items = data.cleanup.filter((c) => c.kind === section.kind);
        if (!items.length) return null;
        const shown = all[section.kind] ? items : items.slice(0, 8);
        return (
          <section key={section.kind} className="panel lib-clean">
            <h3>
              {section.title} <span className="muted">({items.length})</span>
            </h3>
            <p className="muted small">{section.note}</p>
            <ul className="plain">
              {shown.map((item) => {
                const d = byId.get(item.doc_id);
                if (!d) return null;
                return (
                  <li key={item.doc_id} className="lib-clean-item">
                    <div className="grow">
                      <Link to={`/library/${d.id}`}>{d.title}</Link>{" "}
                      <span className="muted small">
                        {where(d)} · {bytes(d.bytes)}
                      </span>
                      <div className="small">{item.why}</div>
                    </div>
                    <div className="row wrap end">
                      <button className="btn small" onClick={() => onShow(d.id)}>
                        Show on map
                      </button>
                      {item.kind === "same_file" &&
                        (d.source_id !== null ? (
                          <span className="muted small">Delete it in {sources.get(d.source_id) ?? "its source"} to remove it here.</span>
                        ) : confirming === d.id ? (
                          <>
                            <button className="btn small danger" onClick={() => onDelete(item)}>
                              Delete this copy
                            </button>
                            <button className="btn small ghost" onClick={() => setConfirming(null)}>
                              Keep
                            </button>
                          </>
                        ) : (
                          <button className="btn small ghost danger-text" onClick={() => setConfirming(d.id)} aria-label={`Delete the copy “${d.title}”`}>
                            Delete…
                          </button>
                        ))}
                    </div>
                  </li>
                );
              })}
            </ul>
            {items.length > shown.length && (
              <button className="btn small ghost" onClick={() => setAll({ ...all, [section.kind]: true })}>
                Show all {items.length}
              </button>
            )}
          </section>
        );
      })}
    </div>
  );
}
