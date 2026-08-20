"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import clsx from "clsx";
import AnswerText from "./AnswerText";
import SourcePanel from "./SourcePanel";
import StepsPanel from "./StepsPanel";
import { readTurn, subscribeChannel } from "@/lib/stream";
import type { Branch, Citation, Doc, Message, Step, User } from "@/lib/types";

type Props = {
  channelId: string;
  initialMessages: Message[];
  documents: Doc[];
  me: User;
};

export default function Chat({ channelId, initialMessages, documents, me }: Props) {
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [viewers, setViewers] = useState<{ user_id: string; name: string }[]>([]);
  const [open, setOpen] = useState<{ citation: Citation; doc: Doc } | null>(null);
  const [branches, setBranches] = useState<Record<string, Branch[]>>({});

  const bottomRef = useRef<HTMLDivElement>(null);
  const streamingId = useRef<string | null>(null);
  const docById = useRef(new Map(documents.map((d) => [d.id, d])));

  useEffect(() => {
    docById.current = new Map(documents.map((d) => [d.id, d]));
  }, [documents]);

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, []);

  const refresh = useCallback(async () => {
    const res = await fetch(`/api/proxy/channels/${channelId}/messages`);
    if (res.ok) setMessages(await res.json());
  }, [channelId]);

  // ---- watch the shared channel -----------------------------------------
  useEffect(() => {
    return subscribeChannel(channelId, {
      presence: (d) => setViewers(d.viewers ?? []),
      message: (d) => {
        // Someone else posted. Our own message is already on screen.
        if (d.author?.id === me.id) return;
        void refresh();
      },
      "branch.reverted": () => void refresh(),
      "branch.switched": () => void refresh(),
      final: (d) => {
        // Another viewer's turn finished; pick up its citations.
        if (streamingId.current !== d.message_id) void refresh();
      },
    });
  }, [channelId, me.id, refresh]);

  // ---- send -------------------------------------------------------------
  const send = useCallback(async () => {
    const content = draft.trim();
    if (!content || busy) return;

    setDraft("");
    setBusy(true);
    setError(null);
    setSteps([]);

    const tempUser: Message = {
      id: `temp-user-${Date.now()}`,
      channel_id: channelId,
      parent_id: null,
      role: "user",
      author: me,
      content,
      status: "complete",
      citations: [],
      created_at: new Date().toISOString(),
      sibling_index: 0,
      sibling_count: 1,
    };
    const tempAssistant: Message = {
      ...tempUser,
      id: `temp-assistant-${Date.now()}`,
      role: "assistant",
      author: null,
      content: "",
      status: "running",
    };
    streamingId.current = tempAssistant.id;
    setMessages((prev) => [...prev, tempUser, tempAssistant]);
    requestAnimationFrame(scrollToBottom);

    try {
      const res = await fetch(`/api/proxy/channels/${channelId}/messages`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!res.ok) throw new Error(await res.text());

      await readTurn(res, {
        onToken: (text) =>
          setMessages((prev) =>
            prev.map((m) =>
              m.id === tempAssistant.id ? { ...m, content: m.content + text } : m,
            ),
          ),
        onStep: (s) => setSteps((prev) => [...prev, s]),
        onRevision: (payload) =>
          setMessages((prev) =>
            prev.map((m) =>
              m.id === tempAssistant.id ? { ...m, content: payload.text } : m,
            ),
          ),
        onFinal: (payload) =>
          setMessages((prev) =>
            prev.map((m) =>
              m.id === tempAssistant.id
                ? { ...m, id: payload.message_id, citations: payload.citations,
                    status: "complete" }
                : m,
            ),
          ),
        onError: (message) => setError(message),
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "답변 생성에 실패했습니다");
    } finally {
      streamingId.current = null;
      setBusy(false);
      // Reconcile optimistic ids and sibling counts with the server.
      void refresh();
    }
  }, [draft, busy, channelId, me, refresh, scrollToBottom]);

  // ---- branching ---------------------------------------------------------
  const revert = useCallback(
    async (messageId: string) => {
      if (!confirm("이 지점으로 되돌립니다. 이후 대화는 삭제되지 않고 다른 갈래로 보존됩니다.")) {
        return;
      }
      const res = await fetch(`/api/proxy/channels/${channelId}/revert`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message_id: messageId }),
      });
      if (res.ok) setMessages(await res.json());
    },
    [channelId],
  );

  const loadBranches = useCallback(
    async (messageId: string) => {
      if (branches[messageId]) return;
      const res = await fetch(
        `/api/proxy/channels/${channelId}/messages/${messageId}/branches`,
      );
      if (res.ok) {
        const data = await res.json();
        setBranches((prev) => ({ ...prev, [messageId]: data }));
      }
    },
    [channelId, branches],
  );

  const switchTo = useCallback(
    async (messageId: string) => {
      const res = await fetch(`/api/proxy/channels/${channelId}/switch/${messageId}`, {
        method: "POST",
      });
      if (res.ok) {
        setMessages(await res.json());
        setBranches({});
      }
    },
    [channelId],
  );

  const openCitation = useCallback((c: Citation) => {
    const doc = docById.current.get(c.document_id);
    if (doc) setOpen({ citation: c, doc });
  }, []);

  useEffect(scrollToBottom, [messages.length, scrollToBottom]);

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        {viewers.length > 1 && (
          <div className="shrink-0 border-b border-line bg-accent-soft px-4 py-1 text-[12px] text-accent">
            👁 {viewers.map((v) => v.name).join(", ")} 님이 함께 보고 있습니다
          </div>
        )}

        <div className="scrollbar-thin flex-1 overflow-y-auto px-4 py-5">
          <div className="mx-auto max-w-3xl space-y-5">
            {messages.length === 0 && <EmptyState documents={documents} onPick={setDraft} />}

            {messages.map((m, i) => (
              <MessageRow
                key={m.id}
                message={m}
                isLast={i === messages.length - 1}
                streaming={busy && m.id === streamingId.current}
                onOpenCitation={openCitation}
                onRevert={revert}
                branches={branches[m.id]}
                onLoadBranches={loadBranches}
                onSwitch={switchTo}
              />
            ))}

            {busy && steps.length > 0 && <StepsPanel steps={steps} />}
            {error && (
              <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-700">
                {error}
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <div className="shrink-0 border-t border-line bg-white px-4 py-3">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  void send();
                }
              }}
              rows={1}
              placeholder={
                documents.length
                  ? "선택한 문서에 대해 질문하세요"
                  : "먼저 문서를 선택하거나 업로드하세요"
              }
              className="max-h-40 min-h-[42px] flex-1 resize-y rounded-lg border border-line px-3 py-2.5 text-[14px] outline-none focus:border-accent"
            />
            <button
              onClick={() => void send()}
              disabled={busy || !draft.trim()}
              className="h-[42px] shrink-0 rounded-lg bg-accent px-4 text-[14px] font-medium text-white disabled:opacity-40"
            >
              {busy ? "답변 중…" : "보내기"}
            </button>
          </div>
          <p className="mx-auto mt-1.5 max-w-3xl text-[11px] text-ink-muted">
            이 채널은 팀 전체에 공개되며, 누가 무엇을 물었는지 함께 표시됩니다.
          </p>
        </div>
      </div>

      {open && (
        <SourcePanel
          doc={open.doc}
          citation={open.citation}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  );
}

function MessageRow({
  message,
  isLast,
  streaming,
  onOpenCitation,
  onRevert,
  branches,
  onLoadBranches,
  onSwitch,
}: {
  message: Message;
  isLast: boolean;
  streaming: boolean;
  onOpenCitation: (c: Citation) => void;
  onRevert: (id: string) => void;
  branches?: Branch[];
  onLoadBranches: (id: string) => void;
  onSwitch: (id: string) => void;
}) {
  const [showBranches, setShowBranches] = useState(false);
  const isUser = message.role === "user";
  const temp = message.id.startsWith("temp-");

  return (
    <div className="group">
      <div className="mb-1 flex items-center gap-2 text-[12px]">
        <span
          className={clsx(
            "flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-semibold",
            isUser ? "bg-gray-200 text-ink-soft" : "bg-accent text-white",
          )}
        >
          {isUser ? (message.author?.name ?? "?").slice(0, 1) : "AI"}
        </span>
        <span className="font-medium text-ink-soft">
          {isUser ? (message.author?.name ?? "알 수 없음") : "에이전트"}
        </span>
        <span className="text-ink-muted">
          {new Date(message.created_at).toLocaleTimeString("ko-KR", {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>

        {message.sibling_count > 1 && (
          <button
            onClick={() => {
              setShowBranches((v) => !v);
              onLoadBranches(message.id);
            }}
            className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-ink-muted hover:bg-gray-200"
            title="다른 갈래 보기"
          >
            ‹ {message.sibling_index + 1}/{message.sibling_count} ›
          </button>
        )}

        {!temp && !isUser && (
          <button
            onClick={() => onRevert(message.id)}
            className="ml-auto rounded px-1.5 py-0.5 text-[11px] text-ink-muted opacity-0 transition-opacity hover:bg-gray-100 group-hover:opacity-100"
            title="이 지점으로 되돌리기"
          >
            ↺ 이 지점부터 다시
          </button>
        )}
      </div>

      {showBranches && branches && (
        <div className="mb-2 ml-7 space-y-1 rounded border border-line bg-gray-50 p-2">
          {branches.map((b) => (
            <button
              key={b.message_id}
              onClick={() => onSwitch(b.message_id)}
              className={clsx(
                "block w-full truncate rounded px-2 py-1 text-left text-[12px]",
                b.is_active ? "bg-accent-soft text-accent" : "hover:bg-white",
              )}
            >
              {b.is_active ? "● " : "○ "}
              {b.preview}
            </button>
          ))}
        </div>
      )}

      <div className="ml-7">
        {isUser ? (
          <p className="whitespace-pre-wrap text-[14.5px] leading-relaxed text-ink">
            {message.content}
          </p>
        ) : (
          <AnswerText
            content={message.content}
            citations={message.citations}
            onOpenCitation={onOpenCitation}
            streaming={streaming}
          />
        )}
      </div>
    </div>
  );
}

function EmptyState({ documents, onPick }: { documents: Doc[]; onPick: (q: string) => void }) {
  const suggestions = documents
    .flatMap((d) => (d.suggested_questions ?? []).map((q) => ({ q, from: d.filename })))
    .slice(0, 5);

  return (
    <div className="py-10 text-center">
      <p className="text-[15px] font-medium text-ink">무엇을 확인해 드릴까요?</p>
      <p className="mt-1 text-[13px] text-ink-muted">
        {documents.length}개 문서를 근거로 답변하고, 문장마다 출처를 표시합니다.
      </p>
      {suggestions.length > 0 && (
        <div className="mx-auto mt-5 max-w-xl space-y-1.5 text-left">
          {suggestions.map(({ q, from }, i) => (
            <button
              key={i}
              onClick={() => onPick(q)}
              className="block w-full rounded-lg border border-line px-3 py-2 text-[13px] hover:border-accent hover:bg-accent-soft"
            >
              {q}
              <span className="ml-2 text-[11px] text-ink-muted">· {from}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
