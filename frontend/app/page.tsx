"use client";

import { useState, useEffect, useRef, useCallback } from "react";

// ── Types ──────────────────────────────────────────────────────────────────────

type BotState = "joining" | "waiting" | "live" | "ended" | "error";

type EventKind = "status" | "transcript" | "action";

interface GlobalEvent {
  id: string;
  meeting_id: string;
  kind: EventKind;
  text: string;
  detail?: string;
  ts: string; // ISO
}

interface Meeting {
  meeting_id: string;
  url: string;
  state: BotState;
  status: string;
  started_at: string;
  transcript: string[];
  actions: { label: string; detail: string }[];
  statusHistory: string[];
}

type WsEvent =
  | { type: "meetings"; meetings: Omit<Meeting, "transcript" | "actions" | "statusHistory">[] }
  | { type: "status";     meeting_id: string; message: string; state: BotState }
  | { type: "transcript"; meeting_id: string; text: string }
  | { type: "assistant";  meeting_id: string; text: string };

// ── Utils ──────────────────────────────────────────────────────────────────────

let _eid = 0;
function eid() { return String(++_eid); }

function elapsedSince(iso: string) {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  return `${Math.floor(s / 60).toString().padStart(2, "0")}:${(s % 60).toString().padStart(2, "0")}`;
}

function timeLabel(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function shortUrl(url: string) {
  return url.replace(/^https?:\/\/meet\.google\.com\//, "");
}

function shortId(id: string) {
  return id.slice(0, 5);
}

// ── State config ───────────────────────────────────────────────────────────────

const STATE: Record<BotState, { label: string; color: string; pulse: boolean }> = {
  joining: { label: "Joining",      color: "bg-amber-400",   pulse: true  },
  waiting: { label: "In the lobby", color: "bg-sky-400",     pulse: true  },
  live:    { label: "Live",         color: "bg-emerald-400", pulse: true  },
  ended:   { label: "Ended",        color: "bg-zinc-600",    pulse: false },
  error:   { label: "Error",        color: "bg-red-500",     pulse: false },
};

const KIND_CONFIG: Record<EventKind, { icon: string; textColor: string; bgColor: string }> = {
  status:     { icon: "·",  textColor: "text-zinc-500",   bgColor: "bg-zinc-800/60"    },
  transcript: { icon: "▸",  textColor: "text-zinc-200",   bgColor: "bg-white/[0.03]"   },
  action:     { icon: "✓",  textColor: "text-emerald-400", bgColor: "bg-emerald-950/40" },
};

// ── Primitives ─────────────────────────────────────────────────────────────────

function Pip({ state }: { state: BotState }) {
  const s = STATE[state];
  return <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${s.color} ${s.pulse ? "animate-pulse" : ""}`} />;
}

function MeetingBadge({ meeting, onClick }: { meeting: Meeting; onClick?: () => void }) {
  const Tag = onClick ? "button" : "span";
  return (
    <Tag
      onClick={onClick}
      className={`inline-flex items-center gap-1.5 font-mono text-[11px] px-2 py-0.5 rounded-md border border-white/[0.08] bg-white/[0.04] text-zinc-400 ${onClick ? "hover:border-white/20 hover:text-white transition-colors" : ""}`}
    >
      <Pip state={meeting.state} />
      {shortUrl(meeting.url) || shortId(meeting.meeting_id)}
    </Tag>
  );
}

// ── Join modal ─────────────────────────────────────────────────────────────────

function JoinModal({ onClose, onJoined }: { onClose: () => void; onJoined: (id: string, url: string) => void }) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => { ref.current?.focus(); }, []);

  const submit = async () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setLoading(true);
    setError("");
    try {
      const res = await fetch("http://localhost:8000/api/join", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ meeting_url: trimmed }),
      });
      if (!res.ok) throw new Error();
      const { meeting_id } = await res.json();
      onJoined(meeting_id, trimmed);
      onClose();
    } catch {
      setError("Can't reach the backend. Run: cd meet_backend && python main.py");
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-lg bg-[#0f0f12] border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="px-6 pt-6 pb-5">
          <h2 className="text-[15px] font-semibold text-white">Add Aria to a meeting</h2>
          <p className="text-[13px] text-zinc-500 mt-1">Aria joins as a silent participant. No Google account needed.</p>

          <input
            ref={ref}
            className="mt-4 w-full bg-white/[0.04] border border-white/[0.08] rounded-xl px-4 py-3 text-[14px] text-white placeholder-zinc-600 outline-none focus:border-white/20 focus:bg-white/[0.06] transition-all"
            placeholder="https://meet.google.com/abc-defg-hij"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />

          {error && <p className="mt-2 text-[12px] text-red-400">{error}</p>}

          <div className="mt-4 flex items-start gap-3 bg-sky-950/40 border border-sky-900/50 rounded-xl px-4 py-3">
            <span className="text-sky-400 mt-0.5 shrink-0">ℹ</span>
            <p className="text-[12px] text-sky-300 leading-relaxed">
              After clicking <strong className="text-sky-200">Add to meeting</strong>, go to <strong className="text-sky-200">People → Waiting</strong> in Meet and admit <strong className="text-sky-200">"Aria Notetaker"</strong>.
            </p>
          </div>
        </div>

        <div className="flex gap-2 px-6 pb-6">
          <button onClick={onClose} className="flex-1 h-10 rounded-xl border border-white/[0.08] text-[13px] text-zinc-400 hover:text-white hover:border-white/20 transition-colors">
            Cancel
          </button>
          <button onClick={submit} disabled={loading || !url.trim()} className="flex-1 h-10 rounded-xl bg-white text-black text-[13px] font-semibold hover:bg-zinc-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors">
            {loading ? "Starting…" : "Add to meeting"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Global activity feed ───────────────────────────────────────────────────────

function ActivityFeed({
  events,
  meetings,
  onSelectMeeting,
}: {
  events: GlobalEvent[];
  meetings: Record<string, Meeting>;
  onSelectMeeting: (id: string) => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  if (events.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-3 text-center">
        <div className="w-10 h-10 rounded-full border border-white/[0.06] flex items-center justify-center">
          <span className="w-2.5 h-2.5 rounded-full bg-zinc-700 animate-pulse" />
        </div>
        <p className="text-[14px] text-zinc-500">No activity yet</p>
        <p className="text-[12px] text-zinc-700">Events from all bots will appear here in real time</p>
      </div>
    );
  }

  // Group events by date boundary
  const rows = events.slice().reverse(); // newest last

  return (
    <div className="divide-y divide-white/[0.04]">
      {rows.map((ev) => {
        const cfg = KIND_CONFIG[ev.kind];
        const meeting = meetings[ev.meeting_id];
        return (
          <div key={ev.id} className={`flex items-start gap-4 px-5 py-3 hover:bg-white/[0.02] transition-colors ${cfg.bgColor}`}>
            {/* Time */}
            <span className="shrink-0 text-[11px] text-zinc-700 font-mono tabular-nums mt-0.5 w-20">
              {timeLabel(ev.ts)}
            </span>

            {/* Meeting badge */}
            {meeting ? (
              <MeetingBadge meeting={meeting} onClick={() => onSelectMeeting(ev.meeting_id)} />
            ) : (
              <span className="inline-flex text-[11px] font-mono text-zinc-700 px-2 py-0.5">{shortId(ev.meeting_id)}</span>
            )}

            {/* Icon */}
            <span className={`shrink-0 text-[13px] mt-0.5 w-4 text-center ${cfg.textColor}`}>{cfg.icon}</span>

            {/* Content */}
            <div className="flex-1 min-w-0">
              <p className={`text-[13px] leading-relaxed ${cfg.textColor}`}>
                {ev.text}
                {ev.detail && <span className="text-zinc-600 ml-2">· {ev.detail}</span>}
              </p>
            </div>
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
}

// ── Meeting card ───────────────────────────────────────────────────────────────

function MeetingCard({ meeting, onClick }: { meeting: Meeting; onClick: () => void }) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (meeting.state !== "live") return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [meeting.state]);

  const s = STATE[meeting.state];

  return (
    <button
      onClick={onClick}
      className="group w-full text-left bg-[#0f0f12] border border-white/[0.07] rounded-2xl p-5 hover:border-white/[0.15] hover:bg-[#131318] transition-all duration-150"
    >
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Pip state={meeting.state} />
          <span className="text-[12px] font-medium text-zinc-400">{s.label}</span>
        </div>
        {(meeting.state === "live" || meeting.state === "ended") && (
          <span className="text-[11px] text-zinc-600 font-mono tabular-nums">{elapsedSince(meeting.started_at)}</span>
        )}
      </div>
      <div className="text-[13px] font-medium text-white font-mono truncate mb-1">{shortUrl(meeting.url)}</div>
      <div className="text-[12px] text-zinc-600 truncate">{meeting.status}</div>
      <div className="mt-5 pt-4 border-t border-white/[0.05] flex items-center justify-between">
        <div className="flex items-center gap-4">
          <span className="text-[12px] text-zinc-600"><span className="text-zinc-300 font-medium tabular-nums">{meeting.transcript.length}</span> lines</span>
          <span className="text-[12px] text-zinc-600"><span className="text-zinc-300 font-medium tabular-nums">{meeting.actions.length}</span> actions</span>
        </div>
        <span className="text-[12px] text-zinc-700 group-hover:text-zinc-400 transition-colors">Open →</span>
      </div>
    </button>
  );
}

// ── Meeting detail ─────────────────────────────────────────────────────────────

function MeetingDetail({
  meeting,
  events,
  onBack,
  onJoin,
}: {
  meeting: Meeting;
  events: GlobalEvent[];
  onBack: () => void;
  onJoin: () => void;
}) {
  const [tab, setTab] = useState<"transcript" | "activity" | "actions">("transcript");
  const [, setTick] = useState(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (meeting.state !== "live") return;
    const t = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [meeting.state]);

  useEffect(() => {
    if (tab === "transcript" || tab === "activity") {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [meeting.transcript.length, events.length, tab]);

  const s = STATE[meeting.state];
  const myEvents = events.filter((e) => e.meeting_id === meeting.meeting_id);

  return (
    <div className="flex flex-col h-screen bg-[#09090e]">
      <header className="shrink-0 h-14 border-b border-white/[0.06] flex items-center justify-between px-5">
        <div className="flex items-center gap-3 min-w-0">
          <button onClick={onBack} className="shrink-0 flex items-center gap-1.5 text-[13px] text-zinc-500 hover:text-white transition-colors">
            <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
            Meetings
          </button>
          <span className="text-white/10">·</span>
          <span className="text-[13px] text-zinc-400 font-mono truncate">{shortUrl(meeting.url)}</span>
        </div>
        <div className="flex items-center gap-4 shrink-0">
          <div className="flex items-center gap-2">
            <Pip state={meeting.state} />
            <span className="text-[13px] text-zinc-400">{s.label}</span>
            {meeting.state === "live" && (
              <span className="text-[13px] text-zinc-600 font-mono tabular-nums">{elapsedSince(meeting.started_at)}</span>
            )}
          </div>
          <button onClick={onJoin} className="h-8 px-3.5 bg-white text-black text-[12px] font-semibold rounded-lg hover:bg-zinc-100 transition-colors">
            + Add meeting
          </button>
        </div>
      </header>

      {meeting.state === "waiting" && (
        <div className="shrink-0 mx-5 mt-4 flex items-center gap-3 bg-sky-950/50 border border-sky-800/60 rounded-xl px-4 py-3">
          <span className="w-2 h-2 rounded-full bg-sky-400 animate-pulse shrink-0" />
          <p className="text-[13px] text-sky-300">
            Aria is in the lobby — go to <strong className="text-sky-200">People → Waiting</strong> in Meet and click <strong className="text-sky-200">Admit</strong>.
          </p>
        </div>
      )}

      <div className="shrink-0 border-b border-white/[0.06] px-5 flex mt-1">
        {(["transcript", "activity", "actions"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-3 text-[13px] font-medium border-b-2 capitalize transition-colors ${
              tab === t ? "border-white text-white" : "border-transparent text-zinc-600 hover:text-zinc-300"
            }`}
          >
            {t}
            {t === "actions" && meeting.actions.length > 0 && (
              <span className="ml-2 bg-white/[0.08] text-zinc-400 text-[11px] rounded-md px-1.5 py-0.5 tabular-nums">{meeting.actions.length}</span>
            )}
            {t === "activity" && myEvents.length > 0 && (
              <span className="ml-2 bg-white/[0.08] text-zinc-400 text-[11px] rounded-md px-1.5 py-0.5 tabular-nums">{myEvents.length}</span>
            )}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Transcript */}
        {tab === "transcript" && (
          <div className="max-w-2xl mx-auto px-5 py-6">
            {meeting.transcript.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 gap-3 text-center">
                <div className="w-10 h-10 rounded-full border border-white/[0.06] flex items-center justify-center">
                  <span className="w-2.5 h-2.5 rounded-full bg-zinc-600 animate-pulse" />
                </div>
                <p className="text-[14px] text-zinc-500">
                  {meeting.state === "waiting" ? "Admit Aria from the lobby to start capturing audio." : "Aria is listening…"}
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {meeting.transcript.map((text, i) => (
                  <div key={i} className="flex gap-3">
                    <span className="shrink-0 mt-1 text-[11px] text-zinc-700 font-mono tabular-nums w-5 text-right">{i + 1}</span>
                    <p className="text-[14px] text-zinc-200 leading-relaxed">{text}</p>
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </div>
        )}

        {/* Activity — this bot's event log */}
        {tab === "activity" && (
          <div>
            {/* Stats */}
            <div className="max-w-2xl mx-auto px-5 py-5">
              <div className="grid grid-cols-3 gap-3 mb-5">
                {[
                  { label: "State",    value: s.label },
                  { label: "Duration", value: meeting.state === "live" ? elapsedSince(meeting.started_at) : "—" },
                  { label: "Events",   value: myEvents.length.toString() },
                ].map((stat) => (
                  <div key={stat.label} className="bg-[#0f0f12] border border-white/[0.07] rounded-xl px-4 py-4">
                    <div className="text-[11px] text-zinc-600 uppercase tracking-wider mb-1.5">{stat.label}</div>
                    <div className="text-[20px] font-semibold text-white tabular-nums">{stat.value}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Event log */}
            <div className="border-t border-white/[0.05]">
              {myEvents.length === 0 ? (
                <p className="text-[13px] text-zinc-700 px-5 py-8 text-center">No events yet</p>
              ) : (
                <div className="divide-y divide-white/[0.04]">
                  {myEvents.map((ev) => {
                    const cfg = KIND_CONFIG[ev.kind];
                    return (
                      <div key={ev.id} className={`flex items-start gap-4 px-5 py-3 ${cfg.bgColor}`}>
                        <span className="shrink-0 text-[11px] text-zinc-700 font-mono tabular-nums mt-0.5 w-20">{timeLabel(ev.ts)}</span>
                        <span className={`shrink-0 text-[13px] mt-0.5 w-4 text-center ${cfg.textColor}`}>{cfg.icon}</span>
                        <div className="flex-1 min-w-0">
                          <p className={`text-[13px] leading-relaxed ${cfg.textColor}`}>
                            {ev.text}
                            {ev.detail && <span className="text-zinc-600 ml-2">· {ev.detail}</span>}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                  <div ref={bottomRef} />
                </div>
              )}
            </div>
          </div>
        )}

        {/* Actions */}
        {tab === "actions" && (
          <div className="max-w-2xl mx-auto px-5 py-6">
            {meeting.actions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 gap-3 text-center">
                <p className="text-[14px] text-zinc-500">No actions yet</p>
                <p className="text-[12px] text-zinc-700">Ask Aria to create a ticket, draft a summary, or search docs</p>
              </div>
            ) : (
              <div className="space-y-2">
                {meeting.actions.map((a, i) => (
                  <div key={i} className="flex items-center gap-3 bg-[#0f0f12] border border-white/[0.07] rounded-xl px-4 py-3.5">
                    <div className="w-5 h-5 rounded-full bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center shrink-0 text-emerald-400 text-[11px]">✓</div>
                    <div className="min-w-0">
                      <div className="text-[13px] font-medium text-white">{a.label}</div>
                      <div className="text-[12px] text-zinc-600 mt-0.5 truncate">{a.detail}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── MCP panel ──────────────────────────────────────────────────────────────────

interface McpServer {
  name: string;
  label: string;
  connected: boolean;
  builtin: boolean;
}

const MCP_ICONS: Record<string, string> = {
  linear: "LN",
  google_drive: "GD",
  jira: "JR",
  slack: "SL",
  gmail: "GM",
};

function McpPanel() {
  const [mcps, setMcps] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedName, setExpandedName] = useState<string | null>(null);
  const [configForm, setConfigForm] = useState<Record<string, { url: string; token: string }>>({});
  const [showAddModal, setShowAddModal] = useState(false);
  const [addForm, setAddForm] = useState({ name: "", label: "", url: "", token: "" });
  const [saving, setSaving] = useState<string | null>(null);

  const fetchMcps = async () => {
    try {
      const res = await fetch("http://localhost:8000/api/mcps");
      if (res.ok) setMcps(await res.json());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchMcps(); }, []);

  const handleConfigure = async (name: string) => {
    const form = configForm[name] ?? { url: "", token: "" };
    setSaving(name);
    try {
      const mcp = mcps.find(m => m.name === name);
      await fetch("http://localhost:8000/api/mcps", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, label: mcp?.label ?? name, url: form.url, token: form.token }),
      });
      await fetchMcps();
      setExpandedName(null);
    } finally {
      setSaving(null);
    }
  };

  const handleDelete = async (name: string) => {
    await fetch(`http://localhost:8000/api/mcps/${name}`, { method: "DELETE" });
    await fetchMcps();
  };

  const handleAdd = async () => {
    setSaving("__add__");
    try {
      await fetch("http://localhost:8000/api/mcps", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(addForm),
      });
      await fetchMcps();
      setShowAddModal(false);
      setAddForm({ name: "", label: "", url: "", token: "" });
    } finally {
      setSaving(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <span className="text-zinc-600 text-[13px]">Loading integrations…</span>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto px-6 py-8">
      <div className="mb-6">
        <h2 className="text-[16px] font-semibold text-white">Integrations</h2>
        <p className="text-[13px] text-zinc-500 mt-1">Manage MCP servers available to the meeting brain. Changes take effect immediately but are not persisted — add to <code className="text-zinc-400 bg-white/[0.05] px-1 py-0.5 rounded">.env</code> for permanence.</p>
      </div>

      <div className="space-y-2">
        {mcps.map((mcp) => (
          <div key={mcp.name} className="bg-[#0f0f12] border border-white/[0.07] rounded-2xl overflow-hidden">
            <div className="flex items-center gap-4 px-5 py-4">
              {/* Icon */}
              <div className="w-9 h-9 rounded-xl bg-white/[0.04] border border-white/[0.08] flex items-center justify-center shrink-0">
                <span className="text-[11px] font-bold text-zinc-400">{MCP_ICONS[mcp.name] ?? mcp.name.slice(0, 2).toUpperCase()}</span>
              </div>

              {/* Name + badge */}
              <div className="flex-1 min-w-0">
                <div className="text-[14px] font-medium text-white">{mcp.label}</div>
                <div className="text-[12px] text-zinc-600 mt-0.5">{mcp.builtin ? "Built-in" : "Custom"}</div>
              </div>

              {/* Status badge */}
              {mcp.connected ? (
                <span className="shrink-0 text-[11px] font-medium text-emerald-400 bg-emerald-950/40 border border-emerald-900/50 px-2.5 py-1 rounded-full">Connected</span>
              ) : (
                <span className="shrink-0 text-[11px] font-medium text-zinc-500 bg-white/[0.03] border border-white/[0.06] px-2.5 py-1 rounded-full">Not configured</span>
              )}

              {/* Configure button */}
              <button
                onClick={() => setExpandedName(expandedName === mcp.name ? null : mcp.name)}
                className="shrink-0 h-8 px-3 text-[12px] text-zinc-400 border border-white/[0.08] rounded-lg hover:text-white hover:border-white/20 transition-colors"
              >
                Configure
              </button>

              {/* Delete (custom only) */}
              {!mcp.builtin && (
                <button
                  onClick={() => handleDelete(mcp.name)}
                  className="shrink-0 h-8 w-8 flex items-center justify-center text-zinc-700 hover:text-red-400 transition-colors rounded-lg border border-white/[0.06] hover:border-red-900/50"
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>

            {/* Inline configure panel */}
            {expandedName === mcp.name && (
              <div className="border-t border-white/[0.06] px-5 py-4 space-y-3 bg-white/[0.01]">
                <div>
                  <label className="block text-[11px] text-zinc-500 uppercase tracking-wider mb-1.5">MCP URL</label>
                  <input
                    className="w-full bg-white/[0.04] border border-white/[0.08] rounded-xl px-4 py-2.5 text-[13px] text-white placeholder-zinc-700 outline-none focus:border-white/20 transition-all"
                    placeholder="https://mcp.example.com"
                    value={configForm[mcp.name]?.url ?? ""}
                    onChange={(e) => setConfigForm(prev => ({ ...prev, [mcp.name]: { ...prev[mcp.name] ?? { token: "" }, url: e.target.value } }))}
                  />
                </div>
                <div>
                  <label className="block text-[11px] text-zinc-500 uppercase tracking-wider mb-1.5">Bearer Token</label>
                  <input
                    type="password"
                    className="w-full bg-white/[0.04] border border-white/[0.08] rounded-xl px-4 py-2.5 text-[13px] text-white placeholder-zinc-700 outline-none focus:border-white/20 transition-all"
                    placeholder="sk-..."
                    value={configForm[mcp.name]?.token ?? ""}
                    onChange={(e) => setConfigForm(prev => ({ ...prev, [mcp.name]: { ...prev[mcp.name] ?? { url: "" }, token: e.target.value } }))}
                  />
                </div>
                {mcp.builtin && (
                  <p className="text-[11px] text-zinc-600">Runtime only. To persist, add <code className="text-zinc-500">{mcp.name.toUpperCase()}_MCP_URL</code> and <code className="text-zinc-500">{mcp.name.toUpperCase()}_MCP_TOKEN</code> to your <code className="text-zinc-500">.env</code>.</p>
                )}
                <div className="flex gap-2 pt-1">
                  <button
                    onClick={() => setExpandedName(null)}
                    className="flex-1 h-9 rounded-xl border border-white/[0.08] text-[12px] text-zinc-400 hover:text-white hover:border-white/20 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => handleConfigure(mcp.name)}
                    disabled={saving === mcp.name}
                    className="flex-1 h-9 rounded-xl bg-white text-black text-[12px] font-semibold hover:bg-zinc-100 disabled:opacity-30 transition-colors"
                  >
                    {saving === mcp.name ? "Saving…" : "Save"}
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Add integration */}
      <button
        onClick={() => setShowAddModal(true)}
        className="mt-4 w-full h-11 rounded-2xl border border-dashed border-white/[0.1] text-[13px] text-zinc-500 hover:text-white hover:border-white/20 transition-colors"
      >
        + Add integration
      </button>

      {/* Add modal */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-4">
          <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={() => setShowAddModal(false)} />
          <div className="relative w-full max-w-lg bg-[#0f0f12] border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
            <div className="px-6 pt-6 pb-5 space-y-3">
              <h2 className="text-[15px] font-semibold text-white">Add custom integration</h2>
              {[
                { key: "name", label: "Name (identifier)", placeholder: "my_server" },
                { key: "label", label: "Display name", placeholder: "My Server" },
                { key: "url", label: "MCP URL", placeholder: "https://mcp.example.com" },
              ].map(({ key, label, placeholder }) => (
                <div key={key}>
                  <label className="block text-[11px] text-zinc-500 uppercase tracking-wider mb-1.5">{label}</label>
                  <input
                    className="w-full bg-white/[0.04] border border-white/[0.08] rounded-xl px-4 py-2.5 text-[13px] text-white placeholder-zinc-700 outline-none focus:border-white/20 transition-all"
                    placeholder={placeholder}
                    value={addForm[key as keyof typeof addForm]}
                    onChange={(e) => setAddForm(prev => ({ ...prev, [key]: e.target.value }))}
                  />
                </div>
              ))}
              <div>
                <label className="block text-[11px] text-zinc-500 uppercase tracking-wider mb-1.5">Bearer Token</label>
                <input
                  type="password"
                  className="w-full bg-white/[0.04] border border-white/[0.08] rounded-xl px-4 py-2.5 text-[13px] text-white placeholder-zinc-700 outline-none focus:border-white/20 transition-all"
                  placeholder="sk-..."
                  value={addForm.token}
                  onChange={(e) => setAddForm(prev => ({ ...prev, token: e.target.value }))}
                />
              </div>
            </div>
            <div className="flex gap-2 px-6 pb-6">
              <button onClick={() => setShowAddModal(false)} className="flex-1 h-10 rounded-xl border border-white/[0.08] text-[13px] text-zinc-400 hover:text-white hover:border-white/20 transition-colors">
                Cancel
              </button>
              <button
                onClick={handleAdd}
                disabled={saving === "__add__" || !addForm.name || !addForm.url || !addForm.token}
                className="flex-1 h-10 rounded-xl bg-white text-black text-[13px] font-semibold hover:bg-zinc-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                {saving === "__add__" ? "Adding…" : "Add integration"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Empty / landing ────────────────────────────────────────────────────────────

// ── Dashboard ──────────────────────────────────────────────────────────────────

function Dashboard({ meetings, events, onSelect, onJoin }: {
  meetings: Meeting[];
  events: GlobalEvent[];
  onSelect: (id: string) => void;
  onJoin: () => void;
}) {
  const active = meetings.filter((m) => ["live", "joining", "waiting"].includes(m.state));
  const past   = meetings.filter((m) => ["ended", "error"].includes(m.state));

  return (
    <div className="min-h-screen bg-[#09090e]">
      {/* Nav */}
      <header className="border-b border-white/[0.06] h-14 flex items-center justify-between px-6">
        <div className="flex items-center gap-2.5">
          <div className="w-6 h-6 rounded-md bg-white flex items-center justify-center">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="black">
              <path d="M12 2a3 3 0 0 1 3 3v4a3 3 0 0 1-6 0V5a3 3 0 0 1 3-3z"/>
              <path d="M19 10a7 7 0 0 1-14 0H3a9 9 0 0 0 8 8.94V21H8v2h8v-2h-3v-2.06A9 9 0 0 0 21 10h-2z"/>
            </svg>
          </div>
          <span className="text-[14px] font-semibold text-white">Aria</span>
        </div>
        <button onClick={onJoin} className="h-8 px-4 bg-white text-black text-[13px] font-semibold rounded-lg hover:bg-zinc-100 transition-colors">
          + Add to meeting
        </button>
      </header>

      {/* Two-column layout: bots left, integrations right — always visible */}
      <div className="max-w-6xl mx-auto px-6 py-8 flex gap-8 items-start">

        {/* Left — bots */}
        <div className="flex-1 min-w-0 space-y-8">

          <section>
            <div className="flex items-center gap-2.5 mb-4">
              <span className="text-[12px] font-medium text-zinc-500 uppercase tracking-wider">Active bots</span>
              {active.length > 0 && (
                <span className="flex items-center gap-1.5 text-[11px] text-emerald-400 font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  {active.length} live
                </span>
              )}
            </div>

            {active.length === 0 ? (
              <button
                onClick={onJoin}
                className="w-full border border-dashed border-white/[0.08] rounded-2xl p-8 flex flex-col items-center gap-3 text-center hover:border-white/20 hover:bg-white/[0.02] transition-all"
              >
                <div className="w-10 h-10 rounded-full border border-white/[0.08] flex items-center justify-center text-zinc-600 text-xl">+</div>
                <div>
                  <p className="text-[14px] font-medium text-zinc-400">No bots running</p>
                  <p className="text-[12px] text-zinc-700 mt-0.5">Add Aria to a Google Meet</p>
                </div>
              </button>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {active.map((m) => <MeetingCard key={m.meeting_id} meeting={m} onClick={() => onSelect(m.meeting_id)} />)}
              </div>
            )}
          </section>

          {past.length > 0 && (
            <section>
              <div className="flex items-center gap-2.5 mb-4">
                <span className="text-[12px] font-medium text-zinc-500 uppercase tracking-wider">Past</span>
                <span className="text-[12px] text-zinc-700">{past.length}</span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {past.map((m) => <MeetingCard key={m.meeting_id} meeting={m} onClick={() => onSelect(m.meeting_id)} />)}
              </div>
            </section>
          )}
        </div>

        {/* Right — integrations panel, always visible */}
        <div className="w-80 shrink-0">
          <div className="flex items-center gap-2.5 mb-4">
            <span className="text-[12px] font-medium text-zinc-500 uppercase tracking-wider">Integrations</span>
          </div>
          <McpPanel />
        </div>

      </div>
    </div>
  );
}

// ── Root ───────────────────────────────────────────────────────────────────────

export default function Home() {
  const [meetings, setMeetings] = useState<Record<string, Meeting>>({});
  const [events, setEvents] = useState<GlobalEvent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showJoin, setShowJoin] = useState(false);

  const addEvent = useCallback((ev: Omit<GlobalEvent, "id" | "ts">) => {
    setEvents((prev) => [...prev, { ...ev, id: eid(), ts: new Date().toISOString() }]);
  }, []);

  const upsert = useCallback((id: string, patch: Partial<Meeting>) => {
    setMeetings((prev) => {
      const base: Meeting = prev[id] ?? {
        meeting_id: id, url: "", state: "joining", status: "Starting…",
        started_at: new Date().toISOString(), transcript: [], actions: [], statusHistory: [],
      };
      return { ...prev, [id]: { ...base, ...patch } };
    });
  }, []);

  useEffect(() => {
    const connect = () => {
      const ws = new WebSocket("ws://localhost:8000/ws");

      ws.onmessage = (e) => {
        const ev = JSON.parse(e.data) as WsEvent;

        if (ev.type === "meetings") {
          setMeetings((prev) => {
            const next: Record<string, Meeting> = {};
            for (const m of ev.meetings) {
              next[m.meeting_id] = {
                ...m,
                transcript:    prev[m.meeting_id]?.transcript    ?? [],
                actions:       prev[m.meeting_id]?.actions       ?? [],
                statusHistory: prev[m.meeting_id]?.statusHistory ?? [],
              };
            }
            return next;
          });
          return;
        }

        const mid = ev.meeting_id;

        if (ev.type === "status") {
          setMeetings((prev) => {
            const m = prev[mid]; if (!m) return prev;
            return { ...prev, [mid]: { ...m, state: ev.state, status: ev.message, statusHistory: [...m.statusHistory, ev.message] } };
          });
          addEvent({ meeting_id: mid, kind: "status", text: ev.message });
        }

        if (ev.type === "transcript") {
          setMeetings((prev) => {
            const m = prev[mid]; if (!m) return prev;
            return { ...prev, [mid]: { ...m, transcript: [...m.transcript, ev.text] } };
          });
          addEvent({ meeting_id: mid, kind: "transcript", text: ev.text });
        }

        if (ev.type === "assistant") {
          setMeetings((prev) => {
            const m = prev[mid]; if (!m) return prev;
            return { ...prev, [mid]: { ...m, actions: [...m.actions, { label: ev.text, detail: "" }] } };
          });
          addEvent({ meeting_id: mid, kind: "action", text: ev.text });
        }
      };

      ws.onclose = () => setTimeout(connect, 2000);
      return ws;
    };

    const ws = connect();
    return () => ws.close();
  }, [addEvent]);

  const handleJoined = (id: string, url: string) => {
    upsert(id, { url, state: "joining", status: "Starting…", statusHistory: ["Starting…"] });
    addEvent({ meeting_id: id, kind: "status", text: "Bot started" });
  };

  const list = Object.values(meetings).sort(
    (a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime()
  );

  const selected = selectedId ? meetings[selectedId] : null;

  return (
    <div className="text-white bg-[#09090e] min-h-screen">
      {selected ? (
        <MeetingDetail
          meeting={selected}
          events={events}
          onBack={() => setSelectedId(null)}
          onJoin={() => setShowJoin(true)}
        />
      ) : (
        <Dashboard meetings={list} events={events} onSelect={setSelectedId} onJoin={() => setShowJoin(true)} />
      )}
      {showJoin && <JoinModal onClose={() => setShowJoin(false)} onJoined={handleJoined} />}
    </div>
  );
}
