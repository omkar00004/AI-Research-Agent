import { useEffect, useState, useRef } from "react";
import {
  Clock,
  Trash2,
  X,
  Plus,
  Search,
  ChevronRight,
  FileText,
  Sparkles,
} from "lucide-react";

/* ---------- Types ---------- */

export interface HistorySession {
  id: string;
  topic: string;
  created_at: string;
  metrics?: { subtasks: number; sources: number; retries: number };
}

interface Props {
  isOpen: boolean;
  onClose: () => void;
  onSelectSession: (session: HistorySession) => void;
  onNewChat: () => void;
  activeSessionId: string | null;
}

/* ---------- Helpers ---------- */

function groupByDate(sessions: HistorySession[]) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86_400_000);
  const sevenDaysAgo = new Date(today.getTime() - 7 * 86_400_000);

  const groups: { label: string; items: HistorySession[] }[] = [
    { label: "Today", items: [] },
    { label: "Yesterday", items: [] },
    { label: "Previous 7 Days", items: [] },
    { label: "Older", items: [] },
  ];

  for (const s of sessions) {
    const d = new Date(s.created_at);
    if (d >= today) groups[0].items.push(s);
    else if (d >= yesterday) groups[1].items.push(s);
    else if (d >= sevenDaysAgo) groups[2].items.push(s);
    else groups[3].items.push(s);
  }

  return groups.filter((g) => g.items.length > 0);
}

function formatTime(iso: string) {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/* ---------- Component ---------- */

export default function HistorySidebar({
  isOpen,
  onClose,
  onSelectSession,
  onNewChat,
  activeSessionId,
}: Props) {
  const [sessions, setSessions] = useState<HistorySession[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const sidebarRef = useRef<HTMLDivElement>(null);

  // Fetch history whenever sidebar opens
  useEffect(() => {
    if (!isOpen) return;
    setLoading(true);
    fetch("/api/history")
      .then((r) => r.json())
      .then((data) => {
        if (Array.isArray(data)) setSessions(data);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [isOpen]);

  // Close on Escape key
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    if (isOpen) window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [isOpen, onClose]);

  async function handleDelete(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    setDeletingId(id);
    try {
      await fetch(`/api/history/${id}`, { method: "DELETE" });
      setSessions((prev) => prev.filter((s) => s.id !== id));
    } catch {
      // ignore
    } finally {
      setDeletingId(null);
    }
  }

  const filtered = search.trim()
    ? sessions.filter((s) =>
        s.topic.toLowerCase().includes(search.toLowerCase())
      )
    : sessions;

  const grouped = groupByDate(filtered);

  return (
    <>
      {/* Backdrop */}
      <div
        className={`sidebar-backdrop ${isOpen ? "sidebar-backdrop-visible" : ""}`}
        onClick={onClose}
      />

      {/* Sidebar panel */}
      <aside
        ref={sidebarRef}
        className={`sidebar-panel ${isOpen ? "sidebar-panel-open" : ""}`}
      >
        {/* Header */}
        <div className="sidebar-header">
          <div className="sidebar-header-title">
            <Clock className="h-5 w-5" strokeWidth={2} />
            <span>Research History</span>
          </div>
          <button onClick={onClose} className="sidebar-close-btn">
            <X className="h-5 w-5" strokeWidth={2.5} />
          </button>
        </div>

        {/* New Chat button */}
        <div className="sidebar-new-chat">
          <button onClick={() => { onNewChat(); onClose(); }} className="sidebar-new-btn">
            <Plus className="h-4 w-4" strokeWidth={2.5} />
            New Research
          </button>
        </div>

        {/* Search */}
        <div className="sidebar-search">
          <Search className="sidebar-search-icon" strokeWidth={2} />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search history…"
            className="sidebar-search-input"
          />
        </div>

        {/* Session list */}
        <div className="sidebar-list">
          {loading ? (
            <div className="sidebar-empty">
              <div className="sidebar-spinner" />
              <span>Loading history…</span>
            </div>
          ) : filtered.length === 0 ? (
            <div className="sidebar-empty">
              <Sparkles className="h-8 w-8 sidebar-empty-icon" strokeWidth={1.5} />
              <span className="sidebar-empty-title">
                {search ? "No matches found" : "No research sessions yet"}
              </span>
              <span className="sidebar-empty-sub">
                {search
                  ? "Try a different search term"
                  : "Start a research to see it here"}
              </span>
            </div>
          ) : (
            grouped.map((group) => (
              <div key={group.label} className="sidebar-group">
                <div className="sidebar-group-label">{group.label}</div>
                {group.items.map((session) => (
                  <button
                    key={session.id}
                    onClick={() => {
                      onSelectSession(session);
                      onClose();
                    }}
                    className={`sidebar-item ${
                      activeSessionId === session.id ? "sidebar-item-active" : ""
                    }`}
                  >
                    <div className="sidebar-item-icon">
                      <FileText className="h-4 w-4" strokeWidth={2} />
                    </div>
                    <div className="sidebar-item-content">
                      <div className="sidebar-item-topic">
                        {session.topic.length > 55
                          ? session.topic.slice(0, 55) + "…"
                          : session.topic}
                      </div>
                      <div className="sidebar-item-meta">
                        <span>{formatTime(session.created_at)}</span>
                        {session.metrics && (
                          <>
                            <span className="sidebar-item-dot">·</span>
                            <span>{session.metrics.sources} sources</span>
                          </>
                        )}
                      </div>
                    </div>
                    <div className="sidebar-item-actions">
                      <ChevronRight className="sidebar-item-chevron" strokeWidth={2} />
                      <button
                        onClick={(e) => handleDelete(session.id, e)}
                        className="sidebar-delete-btn"
                        disabled={deletingId === session.id}
                      >
                        <Trash2 className="h-3.5 w-3.5" strokeWidth={2} />
                      </button>
                    </div>
                  </button>
                ))}
              </div>
            ))
          )}
        </div>

        {/* Footer */}
        <div className="sidebar-footer">
          <span>{sessions.length} session{sessions.length !== 1 ? "s" : ""}</span>
        </div>
      </aside>
    </>
  );
}
