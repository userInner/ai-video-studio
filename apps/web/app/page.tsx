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
  Sparkle,
} from "@phosphor-icons/react";
import { useEffect, useMemo, useState } from "react";

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

const modes: Array<{ id: Mode; label: string; hint: string }> = [
  { id: "title", label: "我有标题", hint: "核验事实，再找角度" },
  { id: "idea", label: "一个模糊想法", hint: "边聊边收紧选题" },
  { id: "inspire", label: "帮我找选题", hint: "从传播机会出发" },
];

const examples = [
  "为什么越来越多年轻人开始反向消费？",
  "今天许家印被判无期徒刑，这件事真正意味着什么？",
  "我想讲 AI，但不知道普通人最关心什么",
];

const stepLabels: Record<string, string> = {
  queued: "排队准备",
  understanding: "理解意图",
  researching: "核验事实",
  synthesizing: "设计角度",
  ready_for_selection: "可以选择",
  preparing_script: "整理素材",
  writing_script: "撰写脚本",
  validating_script: "校验脚本",
  script_ready: "脚本完成",
  waiting_for_script: "等待脚本",
  storyboarding: "规划分镜",
  generating_media: "生成配音与画面",
  composing_video: "合成竖屏视频",
  video_ready: "成片完成",
  failed: "需要重试",
};

export default function Home() {
  const [mode, setMode] = useState<Mode>("title");
  const [input, setInput] = useState("");
  const [project, setProject] = useState<Project | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [duration, setDuration] = useState(300);
  const [miniMaxUrl, setMiniMaxUrl] = useState("https://api.minimax.io");
  const [miniMaxKey, setMiniMaxKey] = useState("");
  const [showMiniMaxKey, setShowMiniMaxKey] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [secureAccess, setSecureAccess] = useState(false);
  const [voiceConnected, setVoiceConnected] = useState(false);
  const [voiceProgress, setVoiceProgress] = useState(0);

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
    setSecureAccess(window.isSecureContext || ["localhost", "127.0.0.1"].includes(window.location.hostname));
    const projectId = new URLSearchParams(window.location.search).get("project");
    if (!projectId) return;
    setBusy(true);
    fetch(`${API}/v1/projects/${projectId}`)
      .then((response) => {
        if (!response.ok) throw new Error("项目不存在");
        return response.json();
      })
      .then(setProject)
      .catch(() => setError("没有找到这个项目，它可能已经被移走。"))
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => {
    if (!project) return;
    const raw = window.sessionStorage.getItem(`minimax:${project.id}`);
    if (!raw) {
      setVoiceConnected(false);
      return;
    }
    try {
      const saved = JSON.parse(raw) as { baseUrl: string; apiKey: string };
      setMiniMaxUrl(saved.baseUrl);
      setMiniMaxKey(saved.apiKey);
      setVoiceConnected(Boolean(saved.apiKey));
    } catch {
      window.sessionStorage.removeItem(`minimax:${project.id}`);
      setVoiceConnected(false);
    }
  }, [project?.id]);

  useEffect(() => {
    if (!project?.latest_run || ["completed", "failed"].includes(project.latest_run.status)) return;
    let active = true;
    const refresh = async () => {
      const response = await fetch(`${API}/v1/projects/${project.id}`);
      if (response.ok && active) setProject(await response.json());
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
  }, [project?.id, project?.latest_run?.status]);

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
      if (!response.ok) throw new Error("创建失败");
      const created = await response.json();
      const projectResponse = await fetch(`${API}/v1/projects/${created.project_id}`);
      window.history.replaceState({}, "", `?project=${created.project_id}`);
      setProject(await projectResponse.json());
    } catch {
      setError("暂时无法开始，请确认本地服务已经启动。所有内容都还在。 ");
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
    else setError(await responseMessage(response, "没有成功启动制作，请稍后重试。"));
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
      if (!planResponse.ok) throw new Error(await responseMessage(planResponse, "配音计划还没有准备好。"));
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
        if (!upload.ok) throw new Error(await responseMessage(upload, `第 ${scene.index + 1} 段配音上传失败。`));
        setVoiceProgress(Math.round(((position + 1) / Math.max(1, pending.length)) * 100));
      }
      const response = await fetch(`${API}/v1/projects/${project.id}/produce-media`, { method: "POST" });
      if (!response.ok) throw new Error(await responseMessage(response, "没有成功启动媒体制作，请稍后重试。"));
      setProject(await response.json());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "浏览器配音没有完成。");
    } finally {
      setBusy(false);
    }
  }

  function miniMaxEndpoint() {
    const parsed = new URL(miniMaxUrl.trim());
    if (parsed.protocol !== "https:") throw new Error("MiniMax API URL 必须使用 HTTPS");
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
    if (!response.ok) throw new Error(`MiniMax 请求失败（${response.status}）`);
    const body = await response.json();
    if (body.base_resp?.status_code !== 0) throw new Error(`MiniMax：${body.base_resp?.status_msg ?? "未知错误"}`);
    const hex = body.data?.audio;
    if (typeof hex !== "string" || !hex.length || hex.length % 2 !== 0) throw new Error("MiniMax 没有返回有效音频");
    const bytes = new Uint8Array(hex.length / 2);
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = Number.parseInt(hex.slice(index * 2, index * 2 + 2), 16);
    return bytes;
  }

  async function connectMiniMax() {
    if (!project || !secureAccess) return;
    setVoiceBusy(true);
    setError("");
    try {
      await synthesizeMiniMax("连接成功");
      window.sessionStorage.setItem(`minimax:${project.id}`, JSON.stringify({ baseUrl: miniMaxUrl.trim(), apiKey: miniMaxKey.trim() }));
      setVoiceConnected(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "MiniMax 连接测试没有通过。");
    } finally {
      setVoiceBusy(false);
    }
  }

  async function disconnectMiniMax() {
    if (!project) return;
    setError("");
    window.sessionStorage.removeItem(`minimax:${project.id}`);
    setMiniMaxKey("");
    setVoiceConnected(false);
  }

  function reset() {
    setProject(null);
    setInput("");
    setError("");
    setMiniMaxKey("");
    setVoiceConnected(false);
    window.history.replaceState({}, "", window.location.pathname);
  }

  return (
    <main className="shell">
      <header className="topbar">
        <button className="brand" onClick={reset} aria-label="返回首页">
          <span className="brandMark"><span /></span>
          <span>传播引擎</span>
          <small>VIDEO INTELLIGENCE</small>
        </button>
        <div className="topMeta">
          <span className="statusDot" /> 本地创作空间
          <button className="ghostButton" onClick={reset}>新建视频</button>
        </div>
      </header>

      {phase === "start" && (
        <section className="startGrid">
          <div className="heroCopy">
            <p className="eyebrow"><Sparkle weight="fill" /> FROM IDEA TO IMPACT</p>
            <h1>把一个念头，<br />做成<span>值得传播</span>的视频。</h1>
            <p className="lede">先聊清楚你真正想表达什么。AI 会核验事实、找出传播角度，再和你确认一张制作卡。</p>

            <div className="modeTabs" role="tablist" aria-label="输入方式">
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
                placeholder={mode === "inspire" ? "告诉我你熟悉的领域、想影响谁；什么都没有也可以……" : "输入标题，或说说你脑子里还没成形的想法……"}
                aria-label="视频想法"
              />
              <div className="promptFooter">
                <span>3–10 分钟 · 抖音竖屏 · AI 白板为默认视觉</span>
                <button onClick={start} disabled={busy || input.trim().length < 2}>
                  {busy ? "正在创建" : "开始拆解"}<ArrowRight weight="bold" />
                </button>
              </div>
            </div>
            {error && <p className="errorText">{error}</p>}
            <div className="examples">
              <span>试试这些</span>
              {examples.map((example) => <button key={example} onClick={() => setInput(example)}>{example}</button>)}
            </div>
          </div>

          <aside className="radarCard" aria-label="传播力雷达">
            <div className="radarHeader"><span>传播力雷达</span><small>LIVE FRAMEWORK</small></div>
            <div className="radar">
              <div className="ring ringOne" /><div className="ring ringTwo" /><div className="ring ringThree" />
              <div className="cross horizontal" /><div className="cross vertical" />
              <div className="radarCore"><Lightning weight="fill" /><b>共鸣点</b><small>WHY SHARE</small></div>
              <span className="radarLabel top">认知反转</span><span className="radarLabel right">利益相关</span>
              <span className="radarLabel bottom">情绪价值</span><span className="radarLabel left">争议张力</span>
              <span className="sweep" />
            </div>
            <div className="radarNote"><MagicWand /> 我们不卖一种画风。我们寻找最适合这个观点的表达方式。</div>
          </aside>
        </section>
      )}

      {phase === "progress" && project && (
        <section className="workingStage">
          <div className="workingPulse"><span /><span /><span /><MagicWand weight="duotone" /></div>
          <p className="eyebrow">DIRECTOR AT WORK</p>
          <h2>先别急着写脚本。<br />我在判断这件事<span>为什么值得讲</span>。</h2>
          <blockquote>“{project.brief}”</blockquote>
          <div className="progressPanel">
            <div className="progressTop"><b>{stepLabels[project.latest_run?.step ?? "queued"]}</b><strong>{project.latest_run?.progress ?? 0}%</strong></div>
            <div className="progressTrack"><span style={{ width: `${project.latest_run?.progress ?? 0}%` }} /></div>
            <div className="progressSteps">
              {["理解意图", "核验事实", "寻找张力", "形成角度"].map((label, index) => (
                <span key={label} className={(project.latest_run?.progress ?? 0) >= [12, 38, 72, 100][index] ? "done" : ""}>
                  <i>{(project.latest_run?.progress ?? 0) >= [12, 38, 72, 100][index] ? <Check /> : index + 1}</i>{label}
                </span>
              ))}
            </div>
          </div>
          {project.latest_run?.status === "failed" && <p className="errorText">{project.latest_run.error ?? "这次调研没有完成，请新建一次重试。"}</p>}
        </section>
      )}

      {phase === "topics" && project && (
        <section className="resultStage">
          <div className="resultIntro">
            <p className="eyebrow"><SealCheck weight="fill" /> TOPIC DIRECTIONS READY</p>
            <h2>同一件事，有三种<br /><span>值得被转发</span>的讲法。</h2>
            <p>先选你最认同的表达目的。这里决定视频的灵魂，后面的脚本和画面都会围绕它展开。</p>
          </div>
          <div className="topicGrid">
            {project.topic_options.map((topic, index) => (
              <article className="topicCard" key={topic.id}>
                <div className="cardNumber">0{index + 1}</div>
                <div className="topicLabel">{topic.label}</div>
                <h3>{topic.title}</h3>
                <p className="hook">“{topic.hook}”</p>
                <div className="topicMeta"><span>{topic.emotion}</span><span>{topic.narrative.length} 段叙事</span></div>
                <p className="insight">{topic.insight}</p>
                <button onClick={() => choose(topic.id)} disabled={busy}>就做这个方向 <ArrowRight /></button>
              </article>
            ))}
          </div>
          {project.fact_note && (
            <div className={`factNote ${isDemoEvidence ? "demo" : ""}`}>
              <div><SealCheck weight="fill" /><b>{isDemoEvidence ? "演示提示" : "事实校正"}</b></div>
              <p>{project.fact_note}</p>
            </div>
          )}
          <div className="evidenceBar">
            <div><SealCheck weight="fill" /><span><b>{isDemoEvidence ? "调研通路待完成" : "事实底稿已建立"}</b><small>{isDemoEvidence ? "当前展示流程演示来源，不会作为正式成片证据" : `当前收录 ${project.sources.length} 条来源，制作前继续深究`}</small></span></div>
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
            <h2>方向定了。<br />确认这张<span>视频制作卡</span>。</h2>
            <p>确认后先完成深度调研和写稿；脚本就绪后，浏览器直连 MiniMax 配音，再调用白板引擎合成视频。</p>
            <div className="safetyNote"><SealCheck /><span>这一刻是人工决策点</span>不会因为一次点击就直接发布到抖音。</div>
          </div>
          <div className="studioColumn"><div className="productionCard">
            <div className="productionTop"><span>制作卡 · V{project.production_card.version}</span><small>{project.production_card.status === "confirmed" ? "已锁定" : "等待确认"}</small></div>
            <label>最终标题<textarea value={project.production_card.title} readOnly /></label>
            <div className="cardField"><span>核心承诺</span><p>{project.production_card.promise}</p></div>
            <div className="cardField"><span>目标观众</span><p>{project.production_card.audience}</p></div>
            <div className="durationField">
              <span><Clock /> 建议时长</span>
              <div>{[180, 300, 480, 600].map((seconds) => <button key={seconds} className={project.production_card?.duration_seconds === seconds ? "active" : ""} onClick={() => updateDuration(seconds)}>{seconds / 60} 分钟</button>)}</div>
            </div>
            <div className="structureField"><span>叙事骨架</span><ol>{project.production_card.structure.map((item) => <li key={item}>{item}</li>)}</ol></div>
            <section className={`voiceAccess ${voiceConnected ? "connected" : ""}`}>
              <div className="voiceAccessHead">
                <div className="voiceStamp"><Key weight="bold" /></div>
                <div><small>VOICE ACCESS · 私有调用凭证</small><b>连接你自己的 MiniMax</b></div>
                <span>{voiceConnected ? "浏览器已连接" : "未连接"}</span>
              </div>
              <p>浏览器直接请求 MiniMax，再把生成的 MP3 交给白板合成器。URL 和 Key 不会发送到我们的服务器。</p>
              <div className="regionSwitch" aria-label="MiniMax 服务区域">
                <button className={miniMaxUrl.includes("minimax.io") ? "active" : ""} onClick={() => { setMiniMaxUrl("https://api.minimax.io"); setVoiceConnected(false); }}>国际站</button>
                <button className={miniMaxUrl.includes("minimaxi.com") ? "active" : ""} onClick={() => { setMiniMaxUrl("https://api.minimaxi.com"); setVoiceConnected(false); }}>中国站</button>
              </div>
              <label className="credentialField">
                <span>API URL</span>
                <input value={miniMaxUrl} onChange={(event) => { setMiniMaxUrl(event.target.value); setVoiceConnected(false); }} spellCheck={false} />
              </label>
              <label className="credentialField">
                <span>API KEY</span>
                <div>
                  <input
                    type={showMiniMaxKey ? "text" : "password"}
                    value={miniMaxKey}
                    onChange={(event) => { setMiniMaxKey(event.target.value); setVoiceConnected(false); }}
                    placeholder="填写你的 MiniMax API Key"
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <button onClick={() => setShowMiniMaxKey((value) => !value)} aria-label={showMiniMaxKey ? "隐藏 API Key" : "显示 API Key"}>
                    {showMiniMaxKey ? <EyeSlash /> : <Eye />}
                  </button>
                </div>
              </label>
              {!secureAccess && <div className="transportWarning">当前是公网 HTTP。为保护 API Key，请配置 HTTPS 域名后再连接。</div>}
              <div className="voiceActions">
                <small>测试会合成“连接成功”四个字，产生极少量费用。</small>
                {voiceConnected && <button className="disconnectVoice" onClick={disconnectMiniMax} disabled={voiceBusy}>清除本地凭证</button>}
                <button className="connectVoice" onClick={connectMiniMax} disabled={voiceBusy || !secureAccess || !miniMaxKey.trim()}>
                  {voiceBusy ? "正在直连" : voiceConnected ? "重新测试" : "保存到当前会话并测试"}
                </button>
              </div>
            </section>
            {project.production_card.status === "confirmed" ? (
              <div className={`confirmedState ${project.production_run?.status === "failed" ? "failed" : ""}`}>
                <SealCheck weight="fill" />
                <div>
                  <b>{project.final_video ? "最新竖屏视频已生成" : project.media_run && !["failed", "completed"].includes(project.media_run.status) ? "脚本已通过，正在生成成片" : project.script ? "第一版完整脚本已生成" : project.production_run?.status === "failed" ? "脚本生成未完成" : "制作卡已确认，正在写稿"}</b>
                  <span>{project.final_video ? `${project.final_video.metadata.voice_provider === "minimax-browser" ? "浏览器直连 MiniMax 配音" : "固定主播配音"}、全程白板绘制、字幕与合成均已完成，可以直接播放预览。` : project.media_run?.error ?? (project.media_run ? stepLabels[project.media_run.step] : project.script ? "事实引用和时长已校验，可以从浏览器生成逐分镜配音。" : project.production_run?.error ?? stepLabels[project.production_run?.step ?? "queued"])}</span>
                </div>
              </div>
            ) : (
              <button className="confirmButton" onClick={confirm} disabled={busy || !voiceConnected}>确认并开始制作 <ArrowRight weight="bold" /></button>
            )}
            {project.production_run && !["completed", "failed"].includes(project.production_run.status) && (
              <div className="productionProgress">
                <div><span>{stepLabels[project.production_run.step] ?? "正在制作"}</span><b>{project.production_run.progress}%</b></div>
                <i><span style={{ width: `${project.production_run.progress}%` }} /></i>
              </div>
            )}
            {project.media_run && !["completed", "failed"].includes(project.media_run.status) && (
              <div className="productionProgress mediaProgress">
                <div><span>{stepLabels[project.media_run.step] ?? "正在制作成片"}</span><b>{project.media_run.progress}%</b></div>
                <i><span style={{ width: `${project.media_run.progress}%` }} /></i>
              </div>
            )}
            {project.production_run?.status === "failed" && !project.script && <button className="retryButton" onClick={confirm} disabled={busy}>重新生成脚本 <ArrowRight /></button>}
            {project.script && !project.final_video && (!project.media_run || project.media_run.status === "failed") && <button className="retryButton mediaButton" onClick={produceMedia} disabled={busy || !voiceConnected}>{busy && voiceProgress > 0 ? `浏览器正在生成配音 ${voiceProgress}%` : project.media_run?.status === "failed" ? "重新生成配音与成片" : "浏览器直连配音并生成成片"} <ArrowRight /></button>}
            {error && <p className="errorText">{error}</p>}
            {selected && <p className="selectedNote">已选择：{selected.label} · {selected.emotion}</p>}
          </div>
          {project.final_video && (
            <article className="videoPreview">
              <div className="videoPreviewTop">
                <div><span>VERTICAL MASTER · V{project.storyboard?.version ?? 1}</span><h3>最新完整视频</h3></div>
                <b>{Math.round(project.final_video.metadata.duration_seconds)} 秒 · 1080 × 1920</b>
              </div>
              <video controls playsInline preload="metadata" poster={project.final_video.poster_url ? `${API}${project.final_video.poster_url}` : undefined} src={`${API}${project.final_video.url}`} />
              <div className="videoMeta">
                <span><SealCheck weight="fill" /> H.264 · AAC · {project.final_video.metadata.fps} FPS</span>
                <span>{project.final_video.metadata.voice_provider === "qwen-local" ? "本地 Qwen 固定主播配音" : project.final_video.metadata.voice_provider === "macos-preview" ? "当前为本机预览声线" : "MiniMax 正式配音"}</span>
                <button onClick={produceMedia} disabled={busy}>重新生成版本</button>
                <a href={`${API}${project.final_video.url}`} download>下载 MP4 <ArrowRight /></a>
              </div>
            </article>
          )}
          {project.storyboard && (
            <article className="storyboardPreview">
              <div className="scriptHeader"><div><span>STORYBOARD · V{project.storyboard.version}</span><h3>全程白板绘制分镜</h3></div><b>{project.storyboard.scenes.length} 个场景</b></div>
              <div className="storyboardGrid">
                {project.storyboard.scenes.map((scene) => {
                  const visual = project.scene_visuals.find((item) => item.scene_index === scene.index);
                  return <div className="storyboardScene" key={scene.index}>
                    {visual && <img src={`${API}${visual.url}`} alt={scene.title} />}
                    <div><small>{String(scene.index + 1).padStart(2, "0")} · {scene.visual_mode.replaceAll("_", " ")}</small><b>{scene.title}</b><span>{Math.round(scene.actual_seconds ?? scene.planned_seconds)} 秒</span></div>
                  </div>;
                })}
              </div>
            </article>
          )}
          {project.script && (
            <article className="scriptPreview">
              <div className="scriptHeader">
                <div><span>SCRIPT · V{project.script.version}</span><h3>{project.script.title}</h3></div>
                <b>{Math.round(project.script.estimated_duration_seconds / 60)} 分钟 · {project.script.sections.length} 段</b>
              </div>
              <div className="scriptThesis"><small>核心论点</small><p>{project.script.thesis}</p></div>
              <div className="scriptSections">
                {project.script.sections.map((section, index) => (
                  <section key={`${section.title}-${index}`}>
                    <div className="scriptIndex">{String(index + 1).padStart(2, "0")}</div>
                    <div className="scriptCopy">
                      <div className="scriptTitle"><h4>{section.title}</h4><time>{section.estimated_seconds} 秒</time></div>
                      <p>{section.narration}</p>
                      <aside><MagicWand /> {section.visual_direction}</aside>
                      {section.claim_source_urls.length > 0 && <div className="scriptSources">{section.claim_source_urls.map((url, sourceIndex) => <a href={url} target="_blank" rel="noreferrer" key={url}><LinkSimple /> 证据 {sourceIndex + 1}</a>)}</div>}
                    </div>
                  </section>
                ))}
              </div>
              <div className="scriptClosing"><small>收束</small><p>{project.script.closing}</p></div>
            </article>
          )}</div>
        </section>
      )}
    </main>
  );
}
