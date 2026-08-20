"use client";

import { Fragment, useMemo } from "react";
import CitationPill from "./CitationPill";
import type { Citation } from "@/lib/types";

/** Matches the token the API stores in message content. */
const CITE_RE = /\[\[cite:(\d{1,3})\]\]/g;

/**
 * Renders an answer with inline citation pills.
 *
 * The markdown handled here is deliberately the small subset the answer prompt
 * asks for — headings, bullets, bold — because a full markdown pipeline would
 * have to be taught to leave the citation tokens alone, and getting that wrong
 * silently drops references.
 */
export default function AnswerText({
  content,
  citations,
  onOpenCitation,
  streaming = false,
}: {
  content: string;
  citations: Citation[];
  onOpenCitation: (c: Citation) => void;
  streaming?: boolean;
}) {
  const byIdx = useMemo(() => {
    const m = new Map<number, Citation>();
    for (const c of citations) m.set(c.idx, c);
    return m;
  }, [citations]);

  const blocks = useMemo(() => splitBlocks(content), [content]);

  return (
    <div className="answer">
      {blocks.map((block, i) => (
        <Block
          key={i}
          block={block}
          byIdx={byIdx}
          onOpenCitation={onOpenCitation}
        />
      ))}
      {streaming && (
        <span className="ml-0.5 inline-block h-[15px] w-[7px] translate-y-[2px] bg-accent animate-pulse-soft" />
      )}
    </div>
  );
}

type Block =
  | { kind: "p"; text: string }
  | { kind: "h"; level: number; text: string }
  | { kind: "ul" | "ol"; items: string[] };

function splitBlocks(content: string): Block[] {
  const lines = content.split("\n");
  const out: Block[] = [];
  let list: { kind: "ul" | "ol"; items: string[] } | null = null;
  let para: string[] = [];

  const flushPara = () => {
    if (para.length) {
      out.push({ kind: "p", text: para.join(" ") });
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      out.push(list);
      list = null;
    }
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (!line.trim()) {
      flushPara();
      flushList();
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      flushPara();
      flushList();
      out.push({ kind: "h", level: heading[1].length, text: heading[2] });
      continue;
    }
    // Nested bullets are flattened: the indentation carries no meaning the
    // reader needs, and preserving it would fight the Korean line-breaking rules.
    const bullet = /^\s*[-*•]\s+(.*)$/.exec(line);
    if (bullet) {
      flushPara();
      if (!list || list.kind !== "ul") {
        flushList();
        list = { kind: "ul", items: [] };
      }
      list.items.push(bullet[1]);
      continue;
    }
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (numbered) {
      flushPara();
      if (!list || list.kind !== "ol") {
        flushList();
        list = { kind: "ol", items: [] };
      }
      list.items.push(numbered[1]);
      continue;
    }
    flushList();
    para.push(line.trim());
  }
  flushPara();
  flushList();
  return out;
}

function Block({
  block,
  byIdx,
  onOpenCitation,
}: {
  block: Block;
  byIdx: Map<number, Citation>;
  onOpenCitation: (c: Citation) => void;
}) {
  const render = (text: string) => (
    <Inline text={text} byIdx={byIdx} onOpenCitation={onOpenCitation} />
  );

  if (block.kind === "h") {
    const size = block.level <= 2 ? "text-[16px]" : "text-[15px]";
    return <div className={`${size} font-semibold mt-4 mb-1.5`}>{render(block.text)}</div>;
  }
  if (block.kind === "p") return <p>{render(block.text)}</p>;

  const List = block.kind === "ol" ? "ol" : "ul";
  return (
    <List>
      {block.items.map((item, i) => (
        <li key={i}>{render(item)}</li>
      ))}
    </List>
  );
}

/** Bold spans and citation pills within one line. */
function Inline({
  text,
  byIdx,
  onOpenCitation,
}: {
  text: string;
  byIdx: Map<number, Citation>;
  onOpenCitation: (c: Citation) => void;
}) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  let key = 0;

  CITE_RE.lastIndex = 0;
  for (let m = CITE_RE.exec(text); m; m = CITE_RE.exec(text)) {
    if (m.index > last) parts.push(<Bold key={key++} text={text.slice(last, m.index)} />);
    const citation = byIdx.get(Number(m[1]));
    if (citation) {
      parts.push(
        <CitationPill key={key++} citation={citation} onOpen={onOpenCitation} />,
      );
    } else {
      // The pill arrives one event after the token that references it; showing a
      // placeholder keeps the text from reflowing when it lands.
      parts.push(
        <span
          key={key++}
          className="mx-[2px] inline-block h-[18px] w-[18px] rounded-[5px] bg-gray-100 align-[1px]"
        />,
      );
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(<Bold key={key++} text={text.slice(last)} />);
  return <>{parts}</>;
}

function Bold({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("**") && p.endsWith("**") && p.length > 4 ? (
          <strong key={i}>{p.slice(2, -2)}</strong>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </>
  );
}
