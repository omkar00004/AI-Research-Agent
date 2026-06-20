import { useEffect, useRef, useState, useCallback } from "react";
import {
  Brain,
  Search,
  ShieldCheck,
  PenLine,
  Download,
  RotateCcw,
  Play,
  Check,
  FileText,
  Menu,
} from "lucide-react";
import MarkdownRenderer from "./MarkdownRenderer";
import HistorySidebar, { type HistorySession } from "./HistorySidebar";

/* ---------------- Types & helpers ---------------- */

type AgentId = "planner" | "researcher" | "critic" | "writer";
type Status = "idle" | "active" | "done" | "revisiting";

interface AgentState {
  id: AgentId;
  name: string;
  role: string;
  num: string;
  Icon: typeof Brain;
  tone: AgentTone;
  status: Status;
  logs: string[];
}

type AgentTone = "primary" | "secondary" | "accent" | "dark";

const TONE: Record<AgentTone, { solid: string; soft: string; text: string; hoverSolid: string; ring: string }> = {
  primary:   { solid: "bg-[#3B82F6]", soft: "bg-[#EFF6FF]", text: "text-[#3B82F6]", hoverSolid: "hover:bg-[#2563EB]", ring: "ring-[#3B82F6]" },
  secondary: { solid: "bg-[#10B981]", soft: "bg-[#ECFDF5]", text: "text-[#10B981]", hoverSolid: "hover:bg-[#059669]", ring: "ring-[#10B981]" },
  accent:    { solid: "bg-[#F59E0B]", soft: "bg-[#FFFBEB]", text: "text-[#F59E0B]", hoverSolid: "hover:bg-[#D97706]", ring: "ring-[#F59E0B]" },
  dark:      { solid: "bg-[#111827]", soft: "bg-[#F3F4F6]", text: "text-[#111827]", hoverSolid: "hover:bg-black",     ring: "ring-[#111827]" },
};

const INITIAL_AGENTS: AgentState[] = [
  { id: "planner",    name: "Planner",    role: "Decomposes the prompt",                num: "01", Icon: Brain,       tone: "primary",   status: "idle", logs: [] },
  { id: "researcher", name: "Researcher", role: "Searches & gathers sources",           num: "02", Icon: Search,      tone: "secondary", status: "idle", logs: [] },
  { id: "critic",     name: "Critic",     role: "Audits coverage and recency",          num: "03", Icon: ShieldCheck, tone: "accent",    status: "idle", logs: [] },
  { id: "writer",     name: "Writer",     role: "Synthesizes the final cited report",   num: "04", Icon: PenLine,     tone: "dark",      status: "idle", logs: [] },
];

interface Source { title: string; url: string; domain: string }
interface Subtask { id: string; title: string; sources: Source[]; done: boolean }
interface Metrics { subtasks: number; sources: number; retries: number }

/* ---- LocalStorage helpers ---- */

const LS_KEY = "atlas_current_session";

interface SavedSession {
  sessionId: string | null;
  topic: string;
  report: string | null;
  subtasks: Subtask[];
  metrics: Metrics | null;
  docxFilename: string | null;
}

function saveToLocalStorage(data: SavedSession) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(data));
  } catch {
    // quota exceeded or private browsing
  }
}

function loadFromLocalStorage(): SavedSession | null {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function clearLocalStorage() {
  try {
    localStorage.removeItem(LS_KEY);
  } catch {
    // ignore
  }
}

/* ---------------- App ---------------- */

export default function App() {
  const [topic, setTopic] = useState("Analyze the impact of LLMs on software engineering jobs");
  const [running, setRunning] = useState(false);
  const [agents, setAgents] = useState<AgentState[]>(INITIAL_AGENTS);
  const [subtasks, setSubtasks] = useState<Subtask[]>([]);
  const [report, setReport] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<AgentId | null>(null);
  const [docxFilename, setDocxFilename] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // ---- Restore from localStorage on mount ----
  useEffect(() => {
    const saved = loadFromLocalStorage();
    if (saved && saved.report) {
      setTopic(saved.topic);
      setReport(saved.report);
      setSubtasks(saved.subtasks || []);
      setMetrics(saved.metrics);
      setDocxFilename(saved.docxFilename);
      setSessionId(saved.sessionId);
      // Mark all agents as done since we have a completed report
      setAgents(INITIAL_AGENTS.map((a) => ({ ...a, status: "done", logs: [] })));
    }
  }, []);

  function patchAgent(id: AgentId, updates: Partial<AgentState>) {
    setAgents((prev) => prev.map((a) => (a.id === id ? { ...a, ...updates } : a)));
  }

  async function run() {
    if (running || !topic.trim()) return;

    // Reset state
    setRunning(true);
    setReport(null);
    setSubtasks([]);
    setMetrics(null);
    setDocxFilename(null);
    setError(null);
    setSessionId(null);
    setAgents(INITIAL_AGENTS.map((a) => ({ ...a, status: "idle", logs: [] })));
    clearLocalStorage();

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch("/api/research", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topic: topic.trim() }),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const jsonStr = line.slice(6).trim();
          if (!jsonStr) continue;

          try {
            const event = JSON.parse(jsonStr);
            handleSSEEvent(event);
          } catch {
            // Skip malformed events
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        setError(err.message);
      }
    } finally {
      setRunning(false);
      setActiveId(null);
      abortRef.current = null;
    }
  }

  function handleSSEEvent(event: Record<string, unknown>) {
    const agentId = event.agent as AgentId | undefined;
    const statuses = event.statuses as Record<string, string> | undefined;
    const logs = event.logs as string[] | undefined;
    const eventSubtasks = event.subtasks as Subtask[] | undefined;
    const eventReport = event.report as string | null | undefined;

    // Update agent statuses
    if (statuses) {
      setAgents((prev) =>
        prev.map((a) => ({
          ...a,
          status: (statuses[a.id] as Status) || a.status,
        }))
      );
    }

    // Update logs for the active agent
    if (agentId && logs && logs.length > 0) {
      setActiveId(agentId);
      patchAgent(agentId, { logs });
    }

    // Update subtasks
    if (eventSubtasks && eventSubtasks.length > 0) {
      setSubtasks(eventSubtasks);
    }

    // Handle completion
    if (event.type === "complete") {
      const completedReport = eventReport || null;
      const completedDocx = (event.docx_filename as string) || null;
      const completedMetrics = (event.metrics as Metrics) || null;
      const completedSubtasks = eventSubtasks || [];
      const completedSessionId = (event.session_id as string) || null;

      if (completedReport) setReport(completedReport);
      if (completedDocx) setDocxFilename(completedDocx);
      if (completedMetrics) setMetrics(completedMetrics);
      if (completedSessionId) setSessionId(completedSessionId);

      // Mark all agents as done
      setAgents((prev) => prev.map((a) => ({ ...a, status: "done" })));
      setActiveId(null);

      // Save to localStorage
      saveToLocalStorage({
        sessionId: completedSessionId,
        topic: (document.querySelector("textarea") as HTMLTextAreaElement)?.value || "",
        report: completedReport,
        subtasks: completedSubtasks,
        metrics: completedMetrics,
        docxFilename: completedDocx,
      });
    }
  }

  function reset() {
    if (abortRef.current) abortRef.current.abort();
    setRunning(false);
    setReport(null);
    setSubtasks([]);
    setActiveId(null);
    setMetrics(null);
    setDocxFilename(null);
    setError(null);
    setSessionId(null);
    setAgents(INITIAL_AGENTS.map((a) => ({ ...a, status: "idle", logs: [] })));
    clearLocalStorage();
  }

  const handleNewChat = useCallback(() => {
    reset();
    setTopic("");
  }, []);

  const handleSelectSession = useCallback(async (session: HistorySession) => {
    // Load full session data from the API
    try {
      const res = await fetch(`/api/history/${session.id}`);
      const data = await res.json();
      if (data.report) {
        setTopic(data.topic);
        setReport(data.report);
        setSubtasks(data.subtasks || []);
        setMetrics(data.metrics || null);
        setDocxFilename(data.docx_filename || null);
        setSessionId(data.id);
        setError(null);
        setRunning(false);
        setActiveId(null);
        setAgents(INITIAL_AGENTS.map((a) => ({ ...a, status: "done", logs: [] })));

        // Save to localStorage
        saveToLocalStorage({
          sessionId: data.id,
          topic: data.topic,
          report: data.report,
          subtasks: data.subtasks || [],
          metrics: data.metrics || null,
          docxFilename: data.docx_filename || null,
        });
      }
    } catch {
      setError("Failed to load session");
    }
  }, []);

  function downloadMd() {
    if (!report) return;
    const blob = new Blob([report], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(topic || "report").toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 60)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function downloadDocx() {
    if (!docxFilename) return;
    window.open(`/api/download/${docxFilename}`, "_blank");
  }

  const progress = agents.filter((a) => a.status === "done").length;

  return (
    <div className="min-h-screen bg-white text-[#111827]" style={{ fontFamily: "'Outfit', sans-serif" }}>
      {/* History Sidebar */}
      <HistorySidebar
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onSelectSession={handleSelectSession}
        onNewChat={handleNewChat}
        activeSessionId={sessionId}
      />

      {/* Top bar */}
      <header className="border-b-2 border-[#111827] bg-white">
        <div className="max-w-7xl mx-auto px-6 md:px-10 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            {/* History toggle button */}
            <button
              onClick={() => setSidebarOpen(true)}
              className="h-10 w-10 grid place-items-center rounded-md bg-[#F3F4F6] hover:bg-[#E5E7EB] transition-colors cursor-pointer"
              title="Research History"
            >
              <Menu className="h-5 w-5 text-[#111827]" strokeWidth={2.5} />
            </button>
            <div className="h-10 w-10 bg-[#3B82F6] grid place-items-center rounded-md">
              <span className="text-white font-extrabold text-lg leading-none">A</span>
            </div>
            <div className="leading-none">
              <div className="font-extrabold text-xl tracking-tight">ATLAS</div>
              <div className="text-[10px] font-semibold tracking-[0.2em] text-[#6B7280] uppercase mt-1">Multi-agent research</div>
            </div>
          </div>
          <div className="hidden md:flex items-center gap-2 text-xs font-semibold uppercase tracking-wider">
            <span className={`h-2.5 w-2.5 rounded-full ${running ? "bg-[#10B981] animate-pulse" : "bg-[#E5E7EB]"}`} />
            <span>{running ? "Team working" : "Idle"}</span>
          </div>
        </div>
      </header>

      {/* HERO: bold blue block */}
      <section className="relative overflow-hidden bg-[#3B82F6] text-white">
        {/* Decorative geometric shapes */}
        <div aria-hidden className="absolute -top-24 -right-24 h-96 w-96 rounded-full bg-white/10" />
        <div aria-hidden className="absolute top-20 right-40 h-32 w-32 rounded-full bg-[#F59E0B]/40" />
        <div aria-hidden className="absolute -bottom-20 left-10 h-64 w-64 rotate-12 bg-white/5 rounded-2xl" />
        <div aria-hidden className="absolute bottom-10 left-1/2 h-20 w-20 rounded-full bg-[#10B981]/60" />

        <div className="relative max-w-7xl mx-auto px-6 md:px-10 pt-16 pb-12">
          <div className="inline-flex items-center gap-2 bg-white text-[#111827] px-3 py-1.5 rounded-md text-xs font-semibold uppercase tracking-wider mb-6">
            <span className="h-2 w-2 rounded-full bg-[#F59E0B]" />
            Live agent orchestration
          </div>
          <h1 className="font-extrabold text-5xl md:text-7xl leading-[0.95] tracking-tight max-w-4xl">
            Four agents.<br />
            One cited report.<br />
            <span className="text-[#FFFBEB]">Watch them work.</span>
          </h1>
          <p className="mt-6 text-lg md:text-xl text-white/90 max-w-2xl font-normal">
            Atlas dispatches a Planner, Researcher, Critic and Writer to investigate any topic - and shows you every handoff in real time.
          </p>

          {/* Prompt card */}
          <div className="mt-10 bg-white text-[#111827] rounded-lg p-6 md:p-7">
            <label className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#6B7280]">Research prompt</label>
            <textarea
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              disabled={running}
              rows={2}
              placeholder="e.g. Analyze the impact of LLMs on software engineering jobs"
              className="mt-2 w-full bg-[#F3F4F6] rounded-md px-4 py-4 text-lg font-medium outline-none border-2 border-transparent focus:bg-white focus:border-[#3B82F6] resize-none placeholder:text-[#9CA3AF] transition-colors"
            />

            {error && (
              <div className="mt-3 px-4 py-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-700 font-medium">
                ⚠ {error}
              </div>
            )}

            <div className="mt-4 flex flex-col sm:flex-row gap-3">
              <button
                onClick={run}
                disabled={running || !topic.trim()}
                className="flex-1 inline-flex items-center justify-center gap-2 bg-[#3B82F6] text-white font-semibold h-14 rounded-md hover:bg-[#2563EB] hover:scale-[1.02] transition-all duration-200 disabled:opacity-40 disabled:hover:scale-100 disabled:cursor-not-allowed text-base cursor-pointer"
              >
                {running ? (
                  <>
                    <span className="h-2 w-2 rounded-full bg-white animate-pulse" />
                    Team in session…
                  </>
                ) : (
                  <>
                    <Play className="h-5 w-5" strokeWidth={2.5} fill="currentColor" />
                    Dispatch the team
                  </>
                )}
              </button>
              {(report || running || subtasks.length > 0) && (
                <button
                  onClick={reset}
                  className="inline-flex items-center justify-center gap-2 bg-[#F3F4F6] text-[#111827] font-semibold h-14 px-6 rounded-md hover:bg-[#E5E7EB] hover:scale-[1.02] transition-all duration-200 cursor-pointer"
                >
                  <RotateCcw className="h-4 w-4" strokeWidth={2.5} />
                  Reset
                </button>
              )}
            </div>
          </div>
        </div>
      </section>

      {/* PIPELINE: agents on white */}
      <section className="bg-white py-16">
        <div className="max-w-7xl mx-auto px-6 md:px-10">
          <div className="flex items-end justify-between mb-8 flex-wrap gap-4">
            <div>
              <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#6B7280] mb-2">The pipeline</div>
              <h2 className="text-3xl md:text-4xl font-extrabold tracking-tight">Planner → Researcher ⇄ Critic → Writer</h2>
            </div>
            <div className="flex items-center gap-3">
              <div className="text-xs font-semibold uppercase tracking-wider text-[#6B7280]">{progress}/4 complete</div>
              <div className="flex gap-1.5">
                {agents.map((a) => (
                  <div
                    key={a.id}
                    className={`h-2 w-10 rounded-sm ${
                      a.status === "done"
                        ? "bg-[#10B981]"
                        : a.status === "active" || a.status === "revisiting"
                        ? "bg-[#F59E0B]"
                        : "bg-[#E5E7EB]"
                    }`}
                  />
                ))}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5">
            {agents.map((a) => (
              <AgentCard key={a.id} agent={a} isActive={activeId === a.id} />
            ))}
          </div>

          {/* Metrics row */}
          {metrics && (
            <div className="mt-8 grid grid-cols-3 gap-4 max-w-lg">
              <div className="bg-[#F3F4F6] rounded-lg p-4 text-center">
                <div className="font-extrabold text-2xl text-[#3B82F6]">{metrics.subtasks}</div>
                <div className="text-[10px] font-bold uppercase tracking-wider text-[#6B7280] mt-1">Subtasks</div>
              </div>
              <div className="bg-[#F3F4F6] rounded-lg p-4 text-center">
                <div className="font-extrabold text-2xl text-[#10B981]">{metrics.sources}</div>
                <div className="text-[10px] font-bold uppercase tracking-wider text-[#6B7280] mt-1">Sources</div>
              </div>
              <div className="bg-[#F3F4F6] rounded-lg p-4 text-center">
                <div className="font-extrabold text-2xl text-[#F59E0B]">{metrics.retries}</div>
                <div className="text-[10px] font-bold uppercase tracking-wider text-[#6B7280] mt-1">Retries</div>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* THREADS: muted block */}
      <section className="bg-[#F3F4F6] py-16">
        <div className="max-w-7xl mx-auto px-6 md:px-10 grid grid-cols-1 lg:grid-cols-5 gap-8">
          <div className="lg:col-span-2">
            <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#6B7280] mb-2">Research threads</div>
            <h2 className="text-3xl font-extrabold tracking-tight mb-6">What we're chasing down</h2>

            {subtasks.length === 0 ? (
              <EmptyBlock
                icon={<Brain className="h-7 w-7 text-[#3B82F6]" strokeWidth={2.5} />}
                title="No threads yet"
                body="The Planner will decompose your prompt into 4–5 focused research threads."
              />
            ) : (
              <ul className="space-y-3">
                {subtasks.map((s, i) => (
                  <li key={s.id} className="bg-white rounded-lg p-5 group hover:scale-[1.01] transition-transform duration-200">
                    <div className="flex items-start gap-4">
                      <div className={`h-10 w-10 shrink-0 rounded-md grid place-items-center font-extrabold text-sm ${
                        s.done ? "bg-[#10B981] text-white" : "bg-[#F3F4F6] text-[#6B7280]"
                      }`}>
                        {s.done ? <Check className="h-5 w-5" strokeWidth={3} /> : String(i + 1).padStart(2, "0")}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="font-semibold text-[15px] leading-snug">{s.title}</div>
                        {s.sources.length > 0 ? (
                          <div className="mt-3 flex flex-wrap gap-1.5">
                            {s.sources.map((src) => (
                              <span key={src.url} className="text-[11px] font-semibold px-2 py-1 rounded bg-[#EFF6FF] text-[#2563EB]">
                                {src.domain}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <div className="mt-3 flex gap-1.5">
                            <div className="h-5 w-20 rounded bg-[#F3F4F6] animate-pulse" />
                            <div className="h-5 w-24 rounded bg-[#F3F4F6] animate-pulse" />
                          </div>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="lg:col-span-3">
            <div className="flex items-end justify-between mb-6 gap-4">
              <div>
                <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#6B7280] mb-2">Final report</div>
                <h2 className="text-3xl font-extrabold tracking-tight">Synthesized & cited</h2>
              </div>
              {report && (
                <div className="flex gap-2">
                  <button
                    onClick={downloadMd}
                    className="inline-flex items-center gap-2 bg-[#111827] text-white font-semibold h-12 px-5 rounded-md hover:bg-black hover:scale-[1.03] transition-all duration-200 cursor-pointer"
                  >
                    <Download className="h-4 w-4" strokeWidth={2.5} />
                    Download .md
                  </button>
                  {docxFilename && (
                    <button
                      onClick={downloadDocx}
                      className="inline-flex items-center gap-2 bg-[#3B82F6] text-white font-semibold h-12 px-5 rounded-md hover:bg-[#2563EB] hover:scale-[1.03] transition-all duration-200 cursor-pointer"
                    >
                      <FileText className="h-4 w-4" strokeWidth={2.5} />
                      Download .docx
                    </button>
                  )}
                </div>
              )}
            </div>

            {!report ? (
              <EmptyBlock
                icon={<PenLine className="h-7 w-7 text-[#F59E0B]" strokeWidth={2.5} />}
                title={running ? "Writer is on standby" : "Awaiting dispatch"}
                body={running ? "The Writer composes once Critic approves the research coverage." : "Hit Dispatch to send the team into the field."}
                tall
              />
            ) : (
              <article className="bg-white rounded-lg p-7 md:p-9 max-h-[720px] overflow-auto scroll-smooth">
                <MarkdownRenderer content={report} />
              </article>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

/* ---------------- Subcomponents ---------------- */

function AgentCard({ agent, isActive }: { agent: AgentState; isActive: boolean }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [agent.logs.length]);

  const tone = TONE[agent.tone];
  const { Icon } = agent;

  const statusLabel =
    agent.status === "done" ? "Complete"
    : agent.status === "active" ? "Working"
    : agent.status === "revisiting" ? "Revisiting"
    : "Waiting";

  const statusBg =
    agent.status === "done" ? "bg-[#10B981] text-white"
    : agent.status === "active" ? "bg-[#F59E0B] text-white"
    : agent.status === "revisiting" ? "bg-[#3B82F6] text-white"
    : "bg-[#F3F4F6] text-[#6B7280]";

  return (
    <div
      className={`relative rounded-lg p-5 transition-all duration-200 ${
        isActive ? `${tone.soft} scale-[1.02]` : "bg-[#F3F4F6] hover:scale-[1.01]"
      }`}
    >
      {/* Top: number + icon + status */}
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className={`h-12 w-12 rounded-md grid place-items-center ${tone.solid} text-white`}>
            <Icon className="h-6 w-6" strokeWidth={2.5} />
          </div>
          <div>
            <div className="text-[10px] font-bold tracking-[0.2em] text-[#6B7280] uppercase">Agent {agent.num}</div>
            <div className="font-extrabold text-lg leading-tight">{agent.name}</div>
          </div>
        </div>
        <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-1 rounded ${statusBg}`}>
          {statusLabel}
        </span>
      </div>

      <p className="text-sm text-[#6B7280] mb-4 font-medium">{agent.role}</p>

      {/* Log feed */}
      <div
        ref={scrollRef}
        className="h-36 overflow-auto rounded-md bg-white p-3"
      >
        {agent.logs.length === 0 ? (
          <div className="font-mono text-[11px] text-[#9CA3AF]">// awaiting handoff</div>
        ) : (
          <ul className="space-y-1.5">
            {agent.logs.map((l, i) => (
              <li key={i} className="font-mono text-[11px] leading-snug text-[#111827]">
                <span className="text-[#9CA3AF] mr-1.5">{String(i + 1).padStart(2, "0")}</span>
                {l}
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Active indicator strip */}
      {isActive && (
        <div className={`absolute left-0 top-0 bottom-0 w-1.5 ${tone.solid} rounded-l-lg`} />
      )}
    </div>
  );
}

function EmptyBlock({ icon, title, body, tall = false }: { icon: React.ReactNode; title: string; body: string; tall?: boolean }) {
  return (
    <div className={`bg-white rounded-lg p-8 flex flex-col items-center justify-center text-center ${tall ? "min-h-[400px]" : "min-h-[220px]"}`}>
      <div className="h-14 w-14 rounded-full bg-[#F3F4F6] grid place-items-center mb-4">{icon}</div>
      <div className="font-extrabold text-lg tracking-tight">{title}</div>
      <div className="text-sm text-[#6B7280] mt-1 max-w-xs">{body}</div>
    </div>
  );
}
