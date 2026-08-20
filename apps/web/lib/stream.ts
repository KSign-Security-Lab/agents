import type { Citation, Step } from "./types";

export type TurnHandlers = {
  onToken?: (text: string) => void;
  onCitation?: (c: { idx: number; filename: string; document_id: string }) => void;
  onStep?: (s: Step) => void;
  onRevision?: (payload: { run_id: string; text: string; citations: unknown[] }) => void;
  onFinal?: (payload: { message_id: string; citations: Citation[] }) => void;
  onError?: (message: string) => void;
};

/**
 * Reads an SSE response body.
 *
 * Hand-rolled rather than using EventSource because the turn is a POST (the
 * question is in the body) and EventSource only issues GETs. The parser has to
 * tolerate an event arriving split across chunk boundaries, which happens
 * routinely once answers get long.
 */
export async function readTurn(res: Response, h: TurnHandlers): Promise<void> {
  if (!res.body) {
    h.onError?.("스트림을 열 수 없습니다");
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const dispatch = (event: string, data: string) => {
    let parsed: any;
    try {
      parsed = JSON.parse(data);
    } catch {
      return;
    }
    switch (event) {
      case "token":
        h.onToken?.(parsed.text ?? "");
        break;
      case "citation":
        h.onCitation?.(parsed);
        break;
      case "step":
        h.onStep?.(parsed);
        break;
      case "revision":
        h.onRevision?.(parsed);
        break;
      case "final":
        h.onFinal?.(parsed);
        break;
      case "error":
        h.onError?.(parsed.message ?? "오류가 발생했습니다");
        break;
    }
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep = buffer.indexOf("\n\n");
    while (sep !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);

      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
      }
      if (dataLines.length) dispatch(event, dataLines.join("\n"));

      sep = buffer.indexOf("\n\n");
    }
  }
}

/**
 * Subscribes to a channel's event stream so other people's messages and the
 * assistant tokens they trigger appear here too.
 */
export function subscribeChannel(
  channelId: string,
  handlers: Record<string, (data: any) => void>,
): () => void {
  const es = new EventSource(`/api/proxy/channels/${channelId}/events`);
  const listeners: [string, EventListener][] = [];
  for (const [event, fn] of Object.entries(handlers)) {
    const listener: EventListener = (e) => {
      try {
        fn(JSON.parse((e as MessageEvent).data));
      } catch {
        /* ignore malformed frame */
      }
    };
    es.addEventListener(event, listener);
    listeners.push([event, listener]);
  }
  return () => {
    for (const [event, listener] of listeners) es.removeEventListener(event, listener);
    es.close();
  };
}
