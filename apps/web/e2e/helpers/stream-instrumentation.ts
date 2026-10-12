/**
 * Browser-side instrumentation for the progressive-output spec.
 *
 * Every function here is passed to `page.addInitScript`, so it is serialized
 * and runs inside the page before the app: it must be self-contained (no
 * imports, no closure over Node values).
 */

/** What the instrumentation exposes on `window` (read via page.evaluate). */
export interface InstrumentedWindow {
  /** DOM log: when the first tab button appeared; tab buttons / panels that
   *  left the document for good (a remount — a moved node is still connected
   *  when the observer runs, so reorders don't count). */
  __vieDom: {
    firstTabAt: number | null;
    removed: Array<{ role: string | null; id: string; t: number }>;
  };
  /** Scripted SSE stream (mocked mode). */
  __vieSse: {
    opened: boolean;
    push: (event: Record<string, unknown>) => void;
    close: () => void;
  };
  /** Tee'd copy of the real SSE stream (live mode). */
  __vieSseLog: Array<{
    event?: string;
    id?: string;
    component?: string;
    final?: boolean;
    provisional?: boolean;
    tabIds?: string[];
    t: number;
  }>;
}

/** Record the first tab button and every tab/panel removal (both modes). */
export function observeTabDom(): void {
  const w = window as unknown as InstrumentedWindow;
  w.__vieDom = { firstTabAt: null, removed: [] };
  const selector = '[role="tab"],[role="tabpanel"]';
  const collect = (node: Node, out: Element[]): void => {
    if (!(node instanceof Element)) return;
    if (node.matches(selector)) out.push(node);
    node.querySelectorAll(selector).forEach((el) => out.push(el));
  };
  const observer = new MutationObserver((records) => {
    const removed: Element[] = [];
    const added: Element[] = [];
    for (const record of records) {
      record.removedNodes.forEach((node) => collect(node, removed));
      record.addedNodes.forEach((node) => collect(node, added));
    }
    for (const el of removed) {
      if (!el.isConnected) w.__vieDom.removed.push({ role: el.getAttribute("role"), id: el.id, t: performance.now() });
    }
    if (w.__vieDom.firstTabAt === null && added.some((el) => el.getAttribute("role") === "tab")) {
      w.__vieDom.firstTabAt = performance.now();
    }
  });
  document.addEventListener("DOMContentLoaded", () => {
    observer.observe(document.body, { childList: true, subtree: true });
  });
}

/** Replace the app's `/videos/:id/stream` fetch with a stream the test writes
 *  to via `window.__vieSse.push(event)` (mocked mode). */
export function scriptSseStream(): void {
  const w = window as unknown as InstrumentedWindow;
  const encoder = new TextEncoder();
  const queued: string[] = [];
  let controller: ReadableStreamDefaultController<Uint8Array> | null = null;
  const send = (chunk: string): void => {
    if (controller) controller.enqueue(encoder.encode(chunk));
    else queued.push(chunk);
  };
  w.__vieSse = {
    opened: false,
    push: (event) => send(`data: ${JSON.stringify(event)}\n\n`),
    close: () => {
      send("data: [DONE]\n\n");
      controller?.close();
    },
  };
  const realFetch = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!/\/videos\/[^/]+\/stream$/.test(url)) return realFetch(input, init);
    w.__vieSse.opened = true;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        controller = c;
        for (const chunk of queued.splice(0)) c.enqueue(encoder.encode(chunk));
      },
    });
    return Promise.resolve(new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
  };
}

/** Tee the real `/videos/:id/stream` response into `window.__vieSseLog` with
 *  arrival times, leaving the app's copy untouched (live mode). */
export function tapSseStream(): void {
  const w = window as unknown as InstrumentedWindow;
  w.__vieSseLog = [];
  const realFetch = window.fetch.bind(window);
  const record = async (stream: ReadableStream<Uint8Array>): Promise<void> => {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.startsWith("data: ") || line === "data: [DONE]") continue;
        try {
          const e = JSON.parse(line.slice(6)) as Record<string, unknown>;
          w.__vieSseLog.push({
            event: e.event as string | undefined,
            id: e.id as string | undefined,
            component: e.component as string | undefined,
            final: e.final as boolean | undefined,
            provisional: e.provisional as boolean | undefined,
            tabIds: e.tabIds as string[] | undefined,
            t: performance.now(),
          });
        } catch {
          w.__vieSseLog.push({ event: "__unparsed", t: performance.now() });
        }
      }
    }
  };
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const res = await realFetch(input, init);
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!/\/videos\/[^/]+\/stream$/.test(url) || !res.body) return res;
    const [forApp, forLog] = res.body.tee();
    record(forLog).catch((err: unknown) => {
      w.__vieSseLog.push({ event: `__tap_error:${String(err)}`, t: performance.now() });
    });
    return new Response(forApp, { status: res.status, statusText: res.statusText, headers: res.headers });
  };
}
