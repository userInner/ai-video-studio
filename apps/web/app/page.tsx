"use client";

import {
  ArrowRight,
  Check,
  Clock,
  Eye,
  EyeSlash,
  Key,
  Lightning,
  LinkSimple,
  MagicWand,
  Play,
  SealCheck,
  SlidersHorizontal,
  Sparkle,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";
import { COPY, EXAMPLES, MODES, STEP_LABELS, TRACE_LABELS, TRACE_TAGS, type Locale } from "./i18n";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8010";

type Mode = "title" | "idea" | "inspire";
type Topic = {
  id: string;
  rank: number;
  label: string;
  title: string;
  hook: string;
  insight: string;
  emotion: string;
  audience: string;
  narrative: string[];
  risk: string;
};
type Source = { id: string; title: string; url: string; publisher: string; credibility: string; summary: string };
type Run = { id: string; status: string; step: string; progress: number; error: string | null };
type TraceEvent = {
  id: number;
  event_type: "progress" | "completed" | "failed" | string;
  message: string;
  created_at: string;
  step?: string;
  progress?: number;
  trace_code?: string;
  detail?: string;
  subject?: string;
  provider?: string;
  source_count?: number;
  publishers?: string[];
  source_titles?: string[];
  corrected_title?: string;
  option_titles?: string[];
  checks?: string[];
  dimensions?: string[];
  error?: string;
};
type ScriptSection = {
  section_type: string;
  title: string;
  purpose: string;
  narration: string;
  visual_direction: string;
  claim_source_urls: string[];
  estimated_seconds: number;
};
type Script = {
  id: string;
  version: number;
  status: string;
  title: string;
  opening_hook: string;
  thesis: string;
  audience_takeaway: string;
  estimated_duration_seconds: number;
  sections: ScriptSection[];
  closing: string;
};
type StoryboardScene = {
  index: number;
  title: string;
  narration: string;
  visual_direction: string;
  visual_mode: string;
  planned_seconds: number;
  actual_seconds?: number;
};
type Project = {
  id: string;
  title: string;
  brief: string;
  stage: string;
  selected_topic_id: string | null;
  fact_note: string | null;
  research_status: "verified" | "demo";
  topic_options: Topic[];
  sources: Source[];
  latest_run: Run | null;
  production_run: Run | null;
  script: Script | null;
  media_run: Run | null;
  storyboard: null | { id: string; version: number; format: string; fps: number; scenes: StoryboardScene[] };
  scene_visuals: Array<{ id: string; scene_index: number; provider: string; model: string; url: string; metadata: Record<string, unknown> }>;
  final_video: null | {
    id: string;
    url: string;
    poster_url: string | null;
    provider: string;
    model: string;
    metadata: { duration_seconds: number; width: number; height: number; fps: number; voice_provider: string; image_provider: string };
  };
  production_card: null | {
    id: string;
    version: number;
    status: string;
    title: string;
    promise: string;
    audience: string;
    duration_seconds: number;
    visual_style: string;
    tone: string;
    structure: string[];
  };
};

export default function Home() {
  const [locale, setLocale] = useState<Locale>("zh");
  const [localeReady, setLocaleReady] = useState(false);
  const [mode, setMode] = useState<Mode>("title");
  const [input, setInput] = useState("");
  const [project, setProject] = useState<Project | null>(null);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [duration, setDuration] = useState(300);
  const [miniMaxUrl, setMiniMaxUrl] = useState("https://api.minimax.io");
  const [miniMaxKey, setMiniMaxKey] = useState("");
  const [showMiniMaxKey, setShowMiniMaxKey] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [voiceError, setVoiceError] = useState("");
  const [secureAccess, setSecureAccess] = useState(false);
  const [voiceConnected, setVoiceConnected] = useState(false);
  const [voiceProgress, setVoiceProgress] = useState(0);
  const t = COPY[locale];
  const modes = MODES[locale];
  const examples = EXAMPLES[locale];
  const stepLabels = STEP_LABELS[locale] as Record<string, string>;
  const traceLabels = TRACE_LABELS[locale] as Record<string, { title: string; detail: string }>;
  const traceTagLabels = TRACE_TAGS[locale] as Record<string, string>;

  const phase = !project ? "start" : project.production_card ? "card" : project.topic_options.length ? "topics" : "progress";
  const selected = useMemo(
    () => project?.topic_options.find((topic) => topic.id === project.selected_topic_id),
    [project],
  );
  const isDemoEvidence = project?.research_status === "demo";

  async function responseMessage(response: Response, fallback: string) {
    try {
      const body = await response.json();
      return typeof body.detail === "string" ? body.detail : fallback;
    } catch {
      return fallback;
    }
  }

  useEffect(() => {
    const savedLocale = window.localStorage.getItem("locale");
    const initialLocale: Locale = savedLocale === "en" || savedLocale === "zh"
      ? savedLocale
      : window.navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
    setLocale(initialLocale);
    setLocaleReady(true);
    setSecureAccess(
      window.location.protocol === "https:" ||
        window.isSecureContext === true ||
        ["localhost", "127.0.0.1"].includes(window.location.hostname),
    );
    const savedVoice = window.sessionStorage.getItem("minimax:session");
    if (savedVoice) {
      try {
        const saved = JSON.parse(savedVoice) as { baseUrl: string; apiKey: string };
        setMiniMaxUrl(saved.baseUrl);
        setMiniMaxKey(saved.apiKey);
        setVoiceConnected(Boolean(saved.apiKey));
      } catch {
        window.sessionStorage.removeItem("minimax:session");
      }
    }
    const projectId = new URLSearchParams(window.location.search).get("project");
    if (!projectId) return;
    setBusy(true);
    fetch(`${API}/v1/projects/${projectId}`)
      .then((response) => {
        if (!response.ok) throw new Error(t.projectMissing);
        return response.json();
      })
      .then(setProject)
      .catch(() => setError(t.projectGone))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => {
    if (!localeReady) return;
    window.localStorage.setItem("locale", locale);
    document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
    document.title = COPY[locale].pageTitle;
  }, [locale, localeReady]);

  useEffect(() => {
    if (!project) return;
    const raw = window.sessionStorage.getItem(`minimax:${project.id}`) ?? window.sessionStorage.getItem("minimax:session");
    if (!raw) {
      return;
    }
    try {
      const saved = JSON.parse(raw) as { baseUrl: string; apiKey: string };
      setMiniMaxUrl(saved.baseUrl);
      setMiniMaxKey(saved.apiKey);
      setVoiceConnected(Boolean(saved.apiKey));
    } catch {
      window.sessionStorage.removeItem(`minimax:${project.id}`);
    }
  }, [project?.id]);

  useEffect(() => {
    if (!settingsOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSettingsOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [settingsOpen]);

  useEffect(() => {
    if (!project?.latest_run?.id) {
      setTraceEvents([]);
      return;
    }
    let active = true;
    fetch(`${API}/v1/runs/${project.latest_run.id}/trace`)
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then((payload: { events: TraceEvent[] }) => {
        if (active) setTraceEvents(payload.events);
      })
      .catch(() => {
        if (active) setTraceEvents([]);
      });
    return () => { active = false; };
  }, [project?.latest_run?.id]);

  useEffect(() => {
    if (!project?.latest_run || ["completed", "failed"].includes(project.latest_run.status)) return;
    let active = true;
    const refresh = async () => {
      const [projectResponse, traceResponse] = await Promise.all([
        fetch(`${API}/v1/projects/${project.id}`),
        fetch(`${API}/v1/runs/${project.latest_run!.id}/trace`),
      ]);
      if (!active) return;
      if (projectResponse.ok) setProject(await projectResponse.json());
      if (traceResponse.ok) {
        const payload = await traceResponse.json() as { events: TraceEvent[] };
        setTraceEvents(payload.events);
      }
    };
    const events = new EventSource(`${API}/v1/runs/${project.latest_run.id}/events`);
    for (const eventName of ["progress", "completed", "failed"]) {
      events.addEventListener(eventName, refresh);
    }
    const fallbackTimer = window.setInterval(refresh, 5000);
    return () => {
      active = false;
      events.close();
      window.clearInterval(fallbackTimer);
    };
  }, [project?.id, project?.latest_run?.id, project?.latest_run?.status]);

  function traceTime(value: string) {
    const hasTimezone = /(?:Z|[+-]\d\d:\d\d)$/.test(value);
    const date = new Date(hasTimezone ? value : `${value}Z`);
    if (Number.isNaN(date.getTime())) return "--:--:--";
    return new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-US", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(date);
  }

  async function start() {
    if (input.trim().length < 2) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API}/v1/projects`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ input: input.trim(), mode }),
      });
      if (!response.ok) throw new Error(t.createFailed);
      const created = await response.json();
      const projectResponse = await fetch(`${API}/v1/projects/${created.project_id}`);
      window.history.replaceState({}, "", `?project=${created.project_id}`);
      setProject(await projectResponse.json());
    } catch {
      setError(t.startFailed);
    } finally {
      setBusy(false);
    }
  }

  async function choose(topicId: string) {
    if (!project) return;
    setBusy(true);
    const response = await fetch(`${API}/v1/projects/${project.id}/select-topic`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ topic_option_id: topicId, duration_seconds: duration }),
    });
    if (response.ok) setProject(await response.json());
    setBusy(false);
  }

  async function confirm() {
    if (!project) return;
    setBusy(true);
    setError("");
    const response = await fetch(`${API}/v1/projects/${project.id}/confirm`, {
      method: "POST",
    });
    if (response.ok) setProject(await response.json());
    else setError(await responseMessage(response, t.productionFailed));
    setBusy(false);
  }

  async function updateDuration(seconds: number) {
    setDuration(seconds);
    if (!project?.production_card || project.production_card.status === "confirmed") return;
    const response = await fetch(`${API}/v1/projects/${project.id}/production-card`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ duration_seconds: seconds }),
    });
    if (response.ok) setProject(await response.json());
  }

  async function produceMedia() {
    if (!project || !voiceConnected) return;
    setBusy(true);
    setError("");
    setVoiceProgress(0);
    try {
      const planResponse = await fetch(`${API}/v1/projects/${project.id}/voice-plan`);
      if (!planResponse.ok) throw new Error(await responseMessage(planResponse, t.voicePlanPending));
      const plan = await planResponse.json() as {
        script_version: number;
        build_version: number;
        scenes: Array<{ index: number; narration: string; uploaded: boolean }>;
      };
      const pending = plan.scenes.filter((scene) => !scene.uploaded);
      for (let position = 0; position < pending.length; position += 1) {
        const scene = pending[position];
        const audio = await synthesizeMiniMax(scene.narration);
        const upload = await fetch(
          `${API}/v1/projects/${project.id}/voice-plan/${plan.script_version}/${plan.build_version}/scenes/${scene.index}`,
          { method: "PUT", headers: { "content-type": "audio/mpeg" }, body: audio },
        );
        if (!upload.ok) throw new Error(await responseMessage(upload, `${t.sceneUploadBefore}${scene.index + 1}${t.sceneUploadAfter}`));
        setVoiceProgress(Math.round(((position + 1) / Math.max(1, pending.length)) * 100));
      }
      const response = await fetch(`${API}/v1/projects/${project.id}/produce-media`, { method: "POST" });
      if (!response.ok) throw new Error(await responseMessage(response, t.mediaFailed));
      setProject(await response.json());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t.browserVoiceFailed);
    } finally {
      setBusy(false);
    }
  }

  function miniMaxEndpoint() {
    const parsed = new URL(miniMaxUrl.trim());
    if (parsed.protocol !== "https:") throw new Error(t.httpsOnly);
    const root = parsed.toString().replace(/\/+$/, "").replace(/\/v1\/t2a_v2$/, "").replace(/\/v1$/, "");
    return `${root}/v1/t2a_v2`;
  }

  async function synthesizeMiniMax(text: string) {
    const response = await fetch(miniMaxEndpoint(), {
      method: "POST",
      headers: { Authorization: `Bearer ${miniMaxKey.trim()}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "speech-2.8-hd",
        text: text.slice(0, 9999),
        stream: false,
        language_boost: "Chinese",
        output_format: "hex",
        voice_setting: { voice_id: "Chinese (Mandarin)_Reliable_Executive", speed: 1.05, vol: 1, pitch: 0 },
        audio_setting: { sample_rate: 32000, bitrate: 128000, format: "mp3", channel: 1 },
      }),
    });
    if (!response.ok) throw new Error(`${t.requestFailed} (${response.status})`);
    const body = await response.json();
    if (body.base_resp?.status_code !== 0) throw new Error(`MiniMax: ${body.base_resp?.status_msg ?? t.unknownError}`);
    const hex = body.data?.audio;
    if (typeof hex !== "string" || !hex.length || hex.length % 2 !== 0) throw new Error(t.invalidAudio);
    const bytes = new Uint8Array(hex.length / 2);
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = Number.parseInt(hex.slice(index * 2, index * 2 + 2), 16);
    return bytes;
  }

  async function connectMiniMax() {
    if (!secureAccess) return;
    setVoiceBusy(true);
    setVoiceError("");
    try {
      await synthesizeMiniMax(t.connectionPhrase);
      const credential = JSON.stringify({ baseUrl: miniMaxUrl.trim(), apiKey: miniMaxKey.trim() });
      window.sessionStorage.setItem("minimax:session", credential);
      if (project) window.sessionStorage.setItem(`minimax:${project.id}`, credential);
      setVoiceConnected(true);
      setSettingsOpen(false);
    } catch (reason) {
      setVoiceError(reason instanceof Error ? reason.message : t.connectionFailed);
    } finally {
      setVoiceBusy(false);
    }
  }

  async function disconnectMiniMax() {
    setVoiceError("");
    window.sessionStorage.removeItem("minimax:session");
    if (project) window.sessionStorage.removeItem(`minimax:${project.id}`);
    setMiniMaxKey("");
    setVoiceConnected(false);
  }

  function reset() {
    setProject(null);
    setInput("");
    setError("");
    window.history.replaceState({}, "", window.location.pathname);
  }

  return (
    <main className="shell">
      <title>{t.pageTitle}</title>
      <header className="topbar">
        <button className="brand" onClick={reset} aria-label={t.home}>
          <span className="brandMark"><span /></span>
          <span>{t.brand}</span>
          <small>VIDEO INTELLIGENCE</small>
        </button>
        <div className="topMeta">
          <span className="statusDot" /> {t.localSpace}
          <div className="languageSwitch" aria-label="Language">
            <button className={locale === "zh" ? "active" : ""} onClick={() => setLocale("zh")} aria-pressed={locale === "zh"}>中</button>
            <button className={locale === "en" ? "active" : ""} onClick={() => setLocale("en")} aria-pressed={locale === "en"}>EN</button>
          </div>
          <button className={`configButton ${voiceConnected ? "connected" : ""}`} onClick={() => { setVoiceError(""); setSettingsOpen(true); }}>
            <SlidersHorizontal weight="bold" />
            <span>{voiceConnected ? t.voiceConnected : t.config}</span>
            <i />
          </button>
          <button className="ghostButton" onClick={reset}>{t.newVideo}</button>
        </div>
      </header>

      {settingsOpen && (
        <div className="settingsScrim" onMouseDown={(event) => { if (event.currentTarget === event.target) setSettingsOpen(false); }}>
          <section className="settingsPanel" role="dialog" aria-modal="true" aria-labelledby="voice-settings-title">
            <div className="settingsTop">
              <div className="settingsTitle">
                <span><Key weight="bold" /></span>
                <div><small>VOICE PROVIDER</small><h2 id="voice-settings-title">{t.settingsTitle}</h2></div>
              </div>
              <button className="settingsClose" onClick={() => setSettingsOpen(false)} aria-label={t.closeSettings}><X weight="bold" /></button>
            </div>

            <div className={`providerStatus ${voiceConnected ? "connected" : ""}`}>
              <div><b>MiniMax</b><span>speech-2.8-hd · {t.mandarin}</span></div>
              <strong>{voiceConnected ? t.sessionConnected : t.waitingConnection}</strong>
            </div>

            <p className="settingsNote">{t.settingsNote}</p>

            <div className="settingsRegion">
              <span>{t.serviceRegion}</span>
              <div className="regionSwitch" aria-label={t.regionAria}>
                <button className={miniMaxUrl.includes("minimax.io") ? "active" : ""} onClick={() => { setMiniMaxUrl("https://api.minimax.io"); setVoiceConnected(false); }}>{t.international}</button>
                <button className={miniMaxUrl.includes("minimaxi.com") ? "active" : ""} onClick={() => { setMiniMaxUrl("https://api.minimaxi.com"); setVoiceConnected(false); }}>{t.china}</button>
              </div>
            </div>

            <label className="settingsField">
              <span>API URL</span>
              <input value={miniMaxUrl} onChange={(event) => { setMiniMaxUrl(event.target.value); setVoiceConnected(false); }} spellCheck={false} />
            </label>
            <label className="settingsField">
              <span>API KEY</span>
              <div>
                <input
                  type={showMiniMaxKey ? "text" : "password"}
                  value={miniMaxKey}
                  onChange={(event) => { setMiniMaxKey(event.target.value); setVoiceConnected(false); }}
                  placeholder={t.keyPlaceholder}
                  autoComplete="off"
                  spellCheck={false}
                />
                <button onClick={() => setShowMiniMaxKey((value) => !value)} aria-label={showMiniMaxKey ? t.hideKey : t.showKey}>
                  {showMiniMaxKey ? <EyeSlash /> : <Eye />}
                </button>
              </div>
            </label>

            {!secureAccess && <div className="transportWarning">{t.httpWarning}</div>}
            {voiceError && <div className="settingsError">{voiceError}</div>}

            <div className="settingsActions">
              <small>{t.testFee}</small>
              {voiceConnected && <button className="disconnectVoice" onClick={disconnectMiniMax} disabled={voiceBusy}>{t.clearCredentials}</button>}
              <button className="connectVoice" onClick={connectMiniMax} disabled={voiceBusy || !secureAccess || !miniMaxKey.trim()}>
                {voiceBusy ? t.connecting : voiceConnected ? t.retest : t.saveTest}
              </button>
            </div>
          </section>
        </div>
      )}

      {phase === "start" && (
        <section className="startGrid">
          <div className="heroCopy">
            <p className="eyebrow"><Sparkle weight="fill" /> FROM IDEA TO IMPACT</p>
            <h1>{t.heroLine1}<br />{t.heroLine2Before}{locale === "en" ? " " : ""}<span>{t.heroEmphasis}</span>{t.heroLine2After}</h1>
            <p className="lede">{t.heroLead}</p>

            <div className="modeTabs" role="tablist" aria-label={t.inputMode}>
              {modes.map((item) => (
                <button key={item.id} className={mode === item.id ? "active" : ""} onClick={() => setMode(item.id)}>
                  <strong>{item.label}</strong><span>{item.hint}</span>
                </button>
              ))}
            </div>

            <div className="promptBox">
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") start(); }}
                placeholder={mode === "inspire" ? t.inspirePlaceholder : t.promptPlaceholder}
                aria-label={t.videoIdea}
              />
              <div className="promptFooter">
                <span>{t.formatNote}</span>
                <button onClick={start} disabled={busy || input.trim().length < 2}>
                  {busy ? t.creating : t.start}<ArrowRight weight="bold" />
                </button>
              </div>
            </div>
            {error && <p className="errorText">{error}</p>}
            <div className="examples">
              <span>{t.tryThese}</span>
              {examples.map((example) => <button key={example} onClick={() => setInput(example)}>{example}</button>)}
            </div>
          </div>

          <aside className="radarCard" aria-label={t.radar}>
            <div className="radarHeader"><span>{t.radar}</span><small>LIVE FRAMEWORK</small></div>
            <div className="radar">
              <div className="ring ringOne" /><div className="ring ringTwo" /><div className="ring ringThree" />
              <div className="cross horizontal" /><div className="cross vertical" />
              <div className="radarCore"><Lightning weight="fill" /><b>{t.resonance}</b><small>WHY SHARE</small></div>
              <span className="radarLabel top">{t.cognitiveTurn}</span><span className="radarLabel right">{t.selfInterest}</span>
              <span className="radarLabel bottom">{t.emotionalValue}</span><span className="radarLabel left">{t.tension}</span>
              <span className="sweep" />
            </div>
            <div className="radarNote"><MagicWand /> {t.radarNote}</div>
          </aside>
        </section>
      )}

      {phase === "progress" && project && (
        <section className="workingStage">
          <div className="workingPulse"><span /><span /><span /><MagicWand weight="duotone" /></div>
          <p className="eyebrow">DIRECTOR AT WORK</p>
          <h2>{t.workingLine1}<br />{t.workingBefore}{locale === "en" ? " " : ""}<span>{t.workingEmphasis}</span>{t.workingAfter}</h2>
          <blockquote>“{project.brief}”</blockquote>
          <div className="progressPanel">
            <div className="progressTop"><b>{stepLabels[project.latest_run?.step ?? "queued"]}</b><strong>{project.latest_run?.progress ?? 0}%</strong></div>
            <div className="progressTrack"><span style={{ width: `${project.latest_run?.progress ?? 0}%` }} /></div>
            <div className="progressSteps">
              {t.progressStages.map((label, index) => (
                <span key={label} className={(project.latest_run?.progress ?? 0) >= [12, 38, 72, 100][index] ? "done" : ""}>
                  <i>{(project.latest_run?.progress ?? 0) >= [12, 38, 72, 100][index] ? <Check /> : index + 1}</i>{label}
                </span>
              ))}
            </div>
          </div>
          <section className="tracePanel" aria-label={t.traceTitle}>
            <header className="traceHeader">
              <div>
                <span className="liveBadge"><i /> {t.traceLive}</span>
                <div><b>{t.traceTitle}</b><small>{traceEvents.length} {t.traceRecords}</small></div>
              </div>
              <p><Eye /> {t.traceNotice}</p>
            </header>
            <div className="traceList">
              {traceEvents.length === 0 && <div className="traceEmpty"><i /> {t.traceWaiting}</div>}
              {traceEvents.map((event, index) => {
                const localized = event.trace_code ? traceLabels[event.trace_code] : undefined;
                const isFailed = event.event_type === "failed";
                const isCurrent = index === traceEvents.length - 1 && project.latest_run?.status === "running";
                const tags = event.checks ?? event.dimensions ?? event.publishers ?? [];
                return (
                  <article className={`traceItem${isCurrent ? " current" : ""}${isFailed ? " failed" : ""}`} key={event.id}>
                    <div className="traceRail">
                      <span>{isFailed ? <WarningCircle weight="fill" /> : isCurrent ? <i /> : <Check weight="bold" />}</span>
                    </div>
                    <div className="traceBody">
                      <div className="traceMeta">
                        <time>{traceTime(event.created_at)}</time>
                        <span>{stepLabels[event.step ?? "queued"] ?? event.step}</span>
                        <b>{isCurrent ? t.traceCurrent : t.traceDone}</b>
                      </div>
                      <h3>{localized?.title ?? event.message}</h3>
                      <p>{localized?.detail ?? event.detail}</p>
                      {event.subject && <div className="traceFact"><small>{t.traceSubject}</small><span>“{event.subject}”</span></div>}
                      {event.provider && <div className="traceFact"><small>{t.traceProvider}</small><span>{event.provider}</span></div>}
                      {typeof event.source_count === "number" && (
                        <div className="traceFact"><small>{t.traceSources}</small><span>{event.source_count}</span></div>
                      )}
                      {tags.length > 0 && <div className="traceTags">{tags.map((tag) => <span key={tag}>{traceTagLabels[tag] ?? tag}</span>)}</div>}
                      {event.source_titles && event.source_titles.length > 0 && (
                        <ol className="traceArtifacts">{event.source_titles.map((title) => <li key={title}>{title}</li>)}</ol>
                      )}
                      {event.corrected_title && <div className="traceFact"><small>{t.traceCorrection}</small><span>{event.corrected_title}</span></div>}
                      {event.option_titles && event.option_titles.length > 0 && (
                        <div className="traceArtifactGroup"><small>{t.traceAngles}</small><ol>{event.option_titles.map((title) => <li key={title}>{title}</li>)}</ol></div>
                      )}
                      {event.error && <div className="traceError"><small>{t.traceError}</small><span>{event.error}</span></div>}
                    </div>
                  </article>
                );
              })}
            </div>
          </section>
          {project.latest_run?.status === "failed" && <p className="errorText">{project.latest_run.error ?? t.researchFailed}</p>}
        </section>
      )}

      {phase === "topics" && project && (
        <section className="resultStage">
          <div className="resultIntro">
            <p className="eyebrow"><SealCheck weight="fill" /> TOPIC DIRECTIONS READY</p>
            <h2>{t.topicsLine1}<br />{locale === "en" ? " " : ""}<span>{t.topicsEmphasis}</span>{t.topicsAfter}</h2>
            <p>{t.topicsLead}</p>
          </div>
          <div className="topicGrid">
            {project.topic_options.map((topic, index) => (
              <article className="topicCard" key={topic.id}>
                <div className="cardNumber">0{index + 1}</div>
                <div className="topicLabel">{topic.label}</div>
                <h3>{topic.title}</h3>
                <p className="hook">“{topic.hook}”</p>
                <div className="topicMeta"><span>{topic.emotion}</span><span>{topic.narrative.length} {t.narrativeSections}</span></div>
                <p className="insight">{topic.insight}</p>
                <button onClick={() => choose(topic.id)} disabled={busy}>{t.chooseAngle} <ArrowRight /></button>
              </article>
            ))}
          </div>
          {project.fact_note && (
            <div className={`factNote ${isDemoEvidence ? "demo" : ""}`}>
              <div><SealCheck weight="fill" /><b>{isDemoEvidence ? t.demoHint : t.factCorrection}</b></div>
              <p>{project.fact_note}</p>
            </div>
          )}
          <div className="evidenceBar">
            <div><SealCheck weight="fill" /><span><b>{isDemoEvidence ? t.researchPending : t.factBaseReady}</b><small>{isDemoEvidence ? t.demoSources : `${t.sourceCountBefore} ${project.sources.length} ${t.sourceCountAfter}`}</small></span></div>
            <div className="sourceList">
              {project.sources.slice(0, 3).map((source) => (
                <a key={source.id} href={source.url} target="_blank" rel="noreferrer"><LinkSimple /> {source.publisher || source.title}</a>
              ))}
            </div>
          </div>
        </section>
      )}

      {phase === "card" && project?.production_card && (
        <section className="cardStage">
          <div className="cardIntro">
            <p className="eyebrow"><Play weight="fill" /> PRODUCTION BRIEF</p>
            <h2>{t.cardLine1}<br />{t.cardBefore}{locale === "en" ? " " : ""}<span>{t.cardEmphasis}</span>{t.cardAfter}</h2>
            <p>{t.cardLead}</p>
            <div className="safetyNote"><SealCheck /><span>{t.decisionPoint}</span>{t.decisionNote}</div>
          </div>
          <div className="studioColumn"><div className="productionCard">
            <div className="productionTop"><span>{t.productionCard} · V{project.production_card.version}</span><small>{project.production_card.status === "confirmed" ? t.locked : t.awaitingConfirmation}</small></div>
            <label>{t.finalTitle}<textarea value={project.production_card.title} readOnly /></label>
            <div className="cardField"><span>{t.promise}</span><p>{project.production_card.promise}</p></div>
            <div className="cardField"><span>{t.audience}</span><p>{project.production_card.audience}</p></div>
            <div className="durationField">
              <span><Clock /> {t.duration}</span>
              <div>{[180, 300, 480, 600].map((seconds) => <button key={seconds} className={project.production_card?.duration_seconds === seconds ? "active" : ""} onClick={() => updateDuration(seconds)}>{seconds / 60} {t.minutes}</button>)}</div>
            </div>
            <div className="structureField"><span>{t.structure}</span><ol>{project.production_card.structure.map((item) => <li key={item}>{item}</li>)}</ol></div>
            <section className={`voiceAccess ${voiceConnected ? "connected" : ""}`}>
              <div className="voiceAccessHead">
                <div className="voiceStamp"><Key weight="bold" /></div>
                <div><small>VOICE ACCESS · {t.privateCredentials}</small><b>{voiceConnected ? t.miniMaxReady : t.miniMaxNeeded}</b></div>
                <span>{voiceConnected ? t.browserConnected : t.notConnected}</span>
              </div>
              <p>{voiceConnected ? t.credentialReadyNote : t.credentialNeededNote}</p>
              <button className="voiceConfigure" onClick={() => { setVoiceError(""); setSettingsOpen(true); }}>
                <SlidersHorizontal weight="bold" /> {voiceConnected ? t.viewVoiceConfig : t.configureNow} <ArrowRight weight="bold" />
              </button>
            </section>
            {project.production_card.status === "confirmed" ? (
              <div className={`confirmedState ${project.production_run?.status === "failed" ? "failed" : ""}`}>
                <SealCheck weight="fill" />
                <div>
                  <b>{project.final_video ? t.latestVideoReady : project.media_run && !["failed", "completed"].includes(project.media_run.status) ? t.makingVideo : project.script ? t.firstScriptReady : project.production_run?.status === "failed" ? t.scriptFailed : t.writingNow}</b>
                  <span>{project.final_video ? `${project.final_video.metadata.voice_provider === "minimax-browser" ? t.directMiniMax : t.fixedVoice}${t.videoCompleteNote}` : project.media_run?.error ?? (project.media_run ? stepLabels[project.media_run.step] : project.script ? t.scriptVerified : project.production_run?.error ?? stepLabels[project.production_run?.step ?? "queued"])}</span>
                </div>
              </div>
            ) : (
              <button className="confirmButton" onClick={confirm} disabled={busy || !voiceConnected}>{t.confirmStart} <ArrowRight weight="bold" /></button>
            )}
            {project.production_run && !["completed", "failed"].includes(project.production_run.status) && (
              <div className="productionProgress">
                <div><span>{stepLabels[project.production_run.step] ?? t.making}</span><b>{project.production_run.progress}%</b></div>
                <i><span style={{ width: `${project.production_run.progress}%` }} /></i>
              </div>
            )}
            {project.media_run && !["completed", "failed"].includes(project.media_run.status) && (
              <div className="productionProgress mediaProgress">
                <div><span>{traceEvents.at(-1)?.message ?? stepLabels[project.media_run.step] ?? t.makingFinal}</span><b>{project.media_run.progress}%</b></div>
                <i><span style={{ width: `${project.media_run.progress}%` }} /></i>
                {traceEvents.length > 0 && (
                  <ol className="mediaWorkLog">
                    {traceEvents.slice(-5).reverse().map((event, index) => (
                      <li className={index === 0 ? "active" : ""} key={event.id}>
                        <time>{traceTime(event.created_at)}</time>
                        <span>{event.message}</span>
                        {typeof event.progress === "number" && <b>{event.progress}%</b>}
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            )}
            {project.production_run?.status === "failed" && !project.script && <button className="retryButton" onClick={confirm} disabled={busy}>{t.retryScript} <ArrowRight /></button>}
            {project.script && !project.final_video && (!project.media_run || project.media_run.status === "failed") && <button className="retryButton mediaButton" onClick={produceMedia} disabled={busy || !voiceConnected}>{busy && voiceProgress > 0 ? `${t.browserVoicing} ${voiceProgress}%` : project.media_run?.status === "failed" ? t.retryMedia : t.makeMedia} <ArrowRight /></button>}
            {error && <p className="errorText">{error}</p>}
            {selected && <p className="selectedNote">{t.selected}: {selected.label} · {selected.emotion}</p>}
          </div>
          {project.final_video && (
            <article className="videoPreview">
              <div className="videoPreviewTop">
                <div><span>VERTICAL MASTER · V{project.storyboard?.version ?? 1}</span><h3>{t.latestVideo}</h3></div>
                <b>{Math.round(project.final_video.metadata.duration_seconds)} {t.seconds} · 1080 × 1920</b>
              </div>
              <video controls playsInline preload="metadata" poster={project.final_video.poster_url ? `${API}${project.final_video.poster_url}` : undefined} src={`${API}${project.final_video.url}`} />
              <div className="videoMeta">
                <span><SealCheck weight="fill" /> H.264 · AAC · {project.final_video.metadata.fps} FPS</span>
                <span>{project.final_video.metadata.voice_provider === "qwen-local" ? t.qwenVoice : project.final_video.metadata.voice_provider === "macos-preview" ? t.previewVoice : t.miniMaxVoice}</span>
                <button onClick={produceMedia} disabled={busy}>{t.regenerate}</button>
                <a href={`${API}${project.final_video.url}`} download>{t.download} <ArrowRight /></a>
              </div>
            </article>
          )}
          {project.storyboard && (
            <article className="storyboardPreview">
              <div className="scriptHeader"><div><span>STORYBOARD · V{project.storyboard.version}</span><h3>{t.whiteboardStoryboard}</h3></div><b>{project.storyboard.scenes.length} {t.scenes}</b></div>
              <div className="storyboardGrid">
                {project.storyboard.scenes.map((scene) => {
                  const visual = project.scene_visuals.find((item) => item.scene_index === scene.index);
                  return <div className="storyboardScene" key={scene.index}>
                    {visual && <img src={`${API}${visual.url}`} alt={scene.title} />}
                    <div><small>{String(scene.index + 1).padStart(2, "0")} · {scene.visual_mode.replaceAll("_", " ")}</small><b>{scene.title}</b><span>{Math.round(scene.actual_seconds ?? scene.planned_seconds)} {t.seconds}</span></div>
                  </div>;
                })}
              </div>
            </article>
          )}
          {project.script && (
            <article className="scriptPreview">
              <div className="scriptHeader">
                <div><span>SCRIPT · V{project.script.version}</span><h3>{project.script.title}</h3></div>
                <b>{Math.round(project.script.estimated_duration_seconds / 60)} {t.minutes} · {project.script.sections.length} {t.narrativeSections}</b>
              </div>
              <div className="scriptThesis"><small>{t.coreThesis}</small><p>{project.script.thesis}</p></div>
              <div className="scriptSections">
                {project.script.sections.map((section, index) => (
                  <section key={`${section.title}-${index}`}>
                    <div className="scriptIndex">{String(index + 1).padStart(2, "0")}</div>
                    <div className="scriptCopy">
                      <div className="scriptTitle"><h4>{section.title}</h4><time>{section.estimated_seconds} {t.seconds}</time></div>
                      <p>{section.narration}</p>
                      <aside><MagicWand /> {section.visual_direction}</aside>
                      {section.claim_source_urls.length > 0 && <div className="scriptSources">{section.claim_source_urls.map((url, sourceIndex) => <a href={url} target="_blank" rel="noreferrer" key={url}><LinkSimple /> {t.evidence} {sourceIndex + 1}</a>)}</div>}
                    </div>
                  </section>
                ))}
              </div>
              <div className="scriptClosing"><small>{t.closing}</small><p>{project.script.closing}</p></div>
            </article>
          )}</div>
        </section>
      )}
    </main>
  );
}
