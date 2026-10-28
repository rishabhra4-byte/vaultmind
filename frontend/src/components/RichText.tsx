import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";

export type CiteMap = Map<number, { n: number; title: string }>;

/** Number citations in order of first appearance, so markers match the sources list. One number
 * per document; a citation of a paragraph ([D12.3]) links straight to that paragraph. */
export function numberCitations(text: string, titles: Map<number, string>): CiteMap {
  const map: CiteMap = new Map();
  for (const m of text.matchAll(/\[D(\d+)(?:\.\d+)?\]/g)) {
    const id = Number(m[1]);
    if (!map.has(id)) map.set(id, { n: map.size + 1, title: titles.get(id) ?? `Document ${id}` });
  }
  return map;
}

/**
 * A small, safe renderer for the assistant's replies: paragraphs, bullet lists, **bold**,
 * `code`, and [D12] / [D12.3] citations rendered as numbered links. It builds React elements rather
 * than injecting HTML, so document text can never run script in the page.
 */
export function RichText({ text, cites, headingLevel = 3 }: { text: string; cites: CiteMap; headingLevel?: number }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];

  const flush = () => {
    if (list.length) {
      blocks.push(
        <ul key={`l${blocks.length}`}>
          {list.map((item, i) => (
            <li key={i}>{inline(item, cites)}</li>
          ))}
        </ul>,
      );
      list = [];
    }
  };

  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)$/);
    if (bullet) {
      list.push(bullet[1]);
      continue;
    }
    flush();
    if (!line.trim()) continue;
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      // Real heading elements, so screen-reader users can move between sections. `headingLevel`
      // is the level for "#", chosen to sit under the page's own headings.
      const Tag = `h${Math.min(headingLevel + heading[1].length - 1, 6)}` as "h2";
      blocks.push(
        <Tag key={`h${blocks.length}`} className="rt-heading">
          {inline(heading[2], cites)}
        </Tag>,
      );
      continue;
    }
    blocks.push(<p key={`p${blocks.length}`}>{inline(line, cites)}</p>);
  }
  flush();
  return <div className="rich-text">{blocks}</div>;
}

function inline(text: string, cites: CiteMap): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\s?\[D\d+(?:\.\d+)?\])/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i}>{part.slice(1, -1)}</code>;
    const cite = part.match(/^\s?\[D(\d+)(?:\.(\d+))?\]$/);
    if (cite) {
      const id = Number(cite[1]);
      const c = cites.get(id);
      const paragraph = cite[2] !== undefined ? Number(cite[2]) : null;
      const where = paragraph !== null ? `, paragraph ${paragraph + 1}` : "";
      return (
        <Link
          key={i}
          to={`/library/${id}${paragraph !== null ? `#p${paragraph}` : ""}`}
          className="cite"
          title={`${c?.title ?? "Source"}${where}`}
          aria-label={`Source: ${c?.title ?? "document"}${where}`}
        >
          {c?.n ?? "•"}
        </Link>
      );
    }
    return <Fragment key={i}>{part}</Fragment>;
  });
}
