import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { TopNav } from "../../components/TopNav";
import { api } from "../../lib/api";
import { useI18n } from "../../lib/preferences";

type AgentEvent = {
  kind: "text" | "tool_call" | "tool_result" | "error" | "done";
  payload: Record<string, unknown>;
};

export function CopilotPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { t } = useI18n();
  const [message, setMessage] = useState("");
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [busy, setBusy] = useState(false);

  const logoutMutation = useMutation({
    mutationFn: api.destroySession,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["session"] });
      navigate("/login", { replace: true });
    },
  });

  async function send() {
    if (!message.trim() || busy) return;
    setBusy(true);
    setEvents([]);
    try {
      const response = await fetch("/api/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ message }),
      });
      if (!response.body) {
        setEvents([{ kind: "error", payload: { message: "No stream body" } }]);
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const chunk of chunks) {
          const line = chunk.replace(/^data:\s*/, "");
          if (!line) continue;
          try {
            const parsed = JSON.parse(line) as AgentEvent;
            setEvents((prev) => [...prev, parsed]);
          } catch {
            /* ignore malformed */
          }
        }
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950">
      <TopNav
        breadcrumbs={t("nav.copilot")}
        onHome={() => navigate("/products")}
        onLogout={() => logoutMutation.mutate()}
      />
      <div className="mx-auto flex max-w-4xl flex-col gap-4 p-6">
        <header>
          <h1 className="text-2xl font-semibold">SeftFlow Copilot</h1>
          <p className="text-sm text-zinc-500 dark:text-slate-400">
            Ask the agent to create products, generate copy, or render posters. Tool calls are shown
            in the trace panel for transparency.
          </p>
        </header>

        <div className="flex flex-col gap-2">
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            className="min-h-[100px] w-full rounded border border-zinc-300 bg-white p-3 text-sm dark:border-slate-700 dark:bg-slate-900"
            placeholder="e.g. Create a new product 'Summer Tee', write casual English copy, render a 1024x1024 hero image."
          />
          <button
            type="button"
            onClick={send}
            disabled={busy}
            className="self-end rounded bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? "Running..." : "Send"}
          </button>
        </div>

        <section aria-label="Agent trace" className="flex flex-col gap-2">
          {events.map((event, index) => (
            <article
              key={index}
              className="rounded border border-zinc-200 bg-white p-3 text-sm dark:border-slate-700 dark:bg-slate-900"
            >
              <div className="mb-1 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-slate-400">
                {event.kind}
              </div>
              <pre className="whitespace-pre-wrap break-words text-xs">
                {JSON.stringify(event.payload, null, 2)}
              </pre>
            </article>
          ))}
        </section>
      </div>
    </div>
  );
}