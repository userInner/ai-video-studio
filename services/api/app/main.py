from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .config import get_settings
from .db import SessionLocal, get_session, init_db
from .media_pipeline import MediaPipelineError, build_storyboard
from .media_workflow import MediaProductionRunner
from .models import ArtifactVersion, MediaAsset, Message, ProductionCard, Project, ScriptVersion, Source, StoryboardVersion, TopicOption, WorkflowEvent, WorkflowRun
from .production import ProductionRunner
from .schemas import CreateProjectRequest, ProjectCreated, SelectTopicRequest, UpdateProductionCardRequest
from .workflow import TopicConfirmationRunner


settings = get_settings()
runner = TopicConfirmationRunner(settings)
production_runner = ProductionRunner(settings)
media_runner = MediaProductionRunner(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await runner.recover()
    await production_runner.recover()
    await media_runner.recover()
    yield


app = FastAPI(title="传播引擎 API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "storage": "local", "model": settings.text_model}


@app.post("/v1/projects", response_model=ProjectCreated, status_code=202)
async def create_project(payload: CreateProjectRequest, session: AsyncSession = Depends(get_session)) -> ProjectCreated:
    clean_input = payload.input.strip()
    project = Project(title=clean_input[:240], brief=clean_input, input_mode=payload.mode)
    session.add(project)
    await session.flush()
    session.add(Message(project_id=project.id, role="user", content=clean_input))
    run = WorkflowRun(project_id=project.id)
    session.add(run)
    await session.commit()
    runner.submit(run.id)
    return ProjectCreated(project_id=project.id, run_id=run.id)


async def _project_payload(session: AsyncSession, project_id: str) -> dict:
    project = await session.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.sources), selectinload(Project.topic_options))
    )
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    card = await session.scalar(
        select(ProductionCard).where(ProductionCard.project_id == project.id).order_by(desc(ProductionCard.version)).limit(1)
    )
    discovery_artifact = await session.scalar(
        select(ArtifactVersion).where(
            ArtifactVersion.project_id == project.id,
            ArtifactVersion.artifact_type == "discovery",
        ).order_by(desc(ArtifactVersion.version)).limit(1)
    )
    research_artifact = await session.scalar(
        select(ArtifactVersion).where(
            ArtifactVersion.project_id == project.id,
            ArtifactVersion.artifact_type == "research_pack",
        ).order_by(desc(ArtifactVersion.version)).limit(1)
    )
    script = await session.scalar(
        select(ScriptVersion).where(ScriptVersion.project_id == project.id).order_by(desc(ScriptVersion.version)).limit(1)
    )
    production_run = await session.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.project_id == project.id, WorkflowRun.workflow_type == "production")
        .order_by(desc(WorkflowRun.created_at))
        .limit(1)
    )
    media_run = await session.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.project_id == project.id, WorkflowRun.workflow_type == "media_production")
        .order_by(desc(WorkflowRun.created_at))
        .limit(1)
    )
    storyboard = await session.scalar(
        select(StoryboardVersion).where(StoryboardVersion.project_id == project.id).order_by(desc(StoryboardVersion.version)).limit(1)
    )
    media_assets = (await session.scalars(
        select(MediaAsset).where(MediaAsset.project_id == project.id, MediaAsset.status == "ready").order_by(MediaAsset.created_at)
    )).all()
    final_video = next((item for item in reversed(media_assets) if item.kind == "final_video"), None)
    poster = next((item for item in reversed(media_assets) if item.kind == "poster"), None)
    latest_build_marker = None if storyboard is None else f"/build-{storyboard.version}/"
    latest_run = await session.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.project_id == project.id)
        .order_by(desc(WorkflowRun.created_at))
        .limit(1)
    )
    return {
        "id": project.id,
        "title": project.title,
        "brief": project.brief,
        "stage": project.stage,
        "input_mode": project.input_mode,
        "selected_topic_id": project.selected_topic_id,
        "fact_note": None if discovery_artifact is None else discovery_artifact.metadata_json.get("fact_note"),
        "research_status": "demo" if research_artifact is None or research_artifact.metadata_json.get("is_demo") else "verified",
        "created_at": project.created_at,
        "sources": [{
            "id": source.id, "title": source.title, "url": source.url, "publisher": source.publisher,
            "published_at": source.published_at, "credibility": source.credibility, "summary": source.summary,
        } for source in sorted(project.sources, key=lambda item: item.created_at)],
        "topic_options": [{"id": option.id, "rank": option.rank, "label": option.label, "title": option.title,
            "hook": option.hook, "insight": option.insight, "emotion": option.emotion, "audience": option.audience,
            "narrative": option.narrative, "risk": option.risk} for option in sorted(project.topic_options, key=lambda item: item.rank)],
        "latest_run": None if latest_run is None else {"id": latest_run.id, "status": latest_run.status,
            "step": latest_run.step, "progress": latest_run.progress, "error": latest_run.error},
        "production_card": None if card is None else {"id": card.id, "version": card.version, "status": card.status,
            "title": card.title, "promise": card.promise, "audience": card.audience,
            "duration_seconds": card.duration_seconds, "visual_style": card.visual_style, "tone": card.tone,
            "structure": card.structure},
        "production_run": None if production_run is None else {"id": production_run.id, "status": production_run.status,
            "step": production_run.step, "progress": production_run.progress, "error": production_run.error},
        "script": None if script is None else {"id": script.id, "version": script.version, "status": script.status,
            **script.content_json},
        "media_run": None if media_run is None else {"id": media_run.id, "status": media_run.status,
            "step": media_run.step, "progress": media_run.progress, "error": media_run.error},
        "storyboard": None if storyboard is None else {**storyboard.content_json, "id": storyboard.id,
            "version": storyboard.version},
        "scene_visuals": [{"id": item.id, "scene_index": item.scene_index, "provider": item.provider,
            "model": item.model, "url": f"/v1/assets/{item.id}", "metadata": item.metadata_json}
            for item in media_assets if item.kind == "scene_visual"
            and (latest_build_marker is None or latest_build_marker in item.relative_path)],
        "final_video": None if final_video is None else {"id": final_video.id, "url": f"/v1/assets/{final_video.id}",
            "provider": final_video.provider, "model": final_video.model, "metadata": final_video.metadata_json,
            "poster_url": None if poster is None else f"/v1/assets/{poster.id}"},
    }


@app.get("/v1/projects/{project_id}")
async def get_project(project_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    return await _project_payload(session, project_id)


async def _voice_plan(session: AsyncSession, project_id: str) -> tuple[ScriptVersion, int, dict]:
    project = await session.get(Project, project_id)
    script = await session.scalar(
        select(ScriptVersion).where(ScriptVersion.project_id == project_id).order_by(desc(ScriptVersion.version)).limit(1)
    )
    if project is None or script is None:
        raise HTTPException(status_code=409, detail="完整脚本尚未生成")
    current_version = await session.scalar(
        select(StoryboardVersion.version).where(StoryboardVersion.project_id == project_id).order_by(desc(StoryboardVersion.version)).limit(1)
    )
    sources = (await session.scalars(select(Source).where(Source.project_id == project_id))).all()
    source_payload = [{
        "title": item.title,
        "url": item.url,
        "publisher": item.publisher,
        "published_at": item.published_at,
        "credibility": item.credibility,
        "summary": item.summary,
    } for item in sources]
    try:
        storyboard = build_storyboard(script.content_json, source_payload)
    except MediaPipelineError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return script, (current_version or 0) + 1, storyboard


@app.get("/v1/projects/{project_id}/voice-plan")
async def get_voice_plan(project_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    script, build_version, storyboard = await _voice_plan(session, project_id)
    base = f"projects/{project_id}/media/v{script.version}/build-{build_version}/audio"
    scenes = []
    for scene in storyboard["scenes"]:
        path = settings.asset_root / base / f"scene-{scene['index'] + 1:02d}.mp3"
        scenes.append({
            "index": scene["index"],
            "title": scene["title"],
            "narration": scene["narration"],
            "uploaded": path.is_file(),
        })
    return {
        "script_version": script.version,
        "build_version": build_version,
        "model": settings.tts_model,
        "voice_id": settings.tts_voice_id,
        "scenes": scenes,
    }


@app.put("/v1/projects/{project_id}/voice-plan/{script_version}/{build_version}/scenes/{scene_index}")
async def upload_scene_voice(
    project_id: str,
    script_version: int,
    build_version: int,
    scene_index: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    script, expected_build, storyboard = await _voice_plan(session, project_id)
    if script.version != script_version or expected_build != build_version:
        raise HTTPException(status_code=409, detail="配音计划已经更新，请刷新后重试")
    if scene_index < 0 or scene_index >= len(storyboard["scenes"]):
        raise HTTPException(status_code=404, detail="分镜不存在")
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="MP3 文件超过 20 MB")
    if request.headers.get("content-type", "").split(";", 1)[0] not in {"audio/mpeg", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="只接受 MP3 音频")
    content = await request.body()
    if not 512 <= len(content) <= 20 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="MP3 文件为空或超过 20 MB")
    if not (content.startswith(b"ID3") or (content[0] == 0xFF and content[1] & 0xE0 == 0xE0)):
        raise HTTPException(status_code=422, detail="上传内容不是有效的 MP3 音频")
    relative = (
        f"projects/{project_id}/media/v{script_version}/build-{build_version}/audio/"
        f"scene-{scene_index + 1:02d}.mp3"
    )
    stored = media_runner.pipeline.assets.write_bytes(relative, content)
    return {"scene_index": scene_index, "size_bytes": stored.size, "sha256": stored.sha256}


@app.post("/v1/projects/{project_id}/select-topic")
async def select_topic(project_id: str, payload: SelectTopicRequest, session: AsyncSession = Depends(get_session)) -> dict:
    project = await session.get(Project, project_id)
    option = await session.get(TopicOption, payload.topic_option_id)
    if project is None or option is None or option.project_id != project_id:
        raise HTTPException(status_code=404, detail="选题不存在")
    current_version = await session.scalar(
        select(ProductionCard.version).where(ProductionCard.project_id == project_id).order_by(desc(ProductionCard.version)).limit(1)
    )
    card = ProductionCard(
        project_id=project_id,
        topic_option_id=option.id,
        version=(current_version or 0) + 1,
        title=option.title,
        promise=option.insight,
        audience=option.audience,
        duration_seconds=payload.duration_seconds,
        structure=option.narrative,
    )
    project.selected_topic_id = option.id
    project.stage = "production_card"
    session.add(card)
    await session.commit()
    return await _project_payload(session, project_id)


@app.post("/v1/projects/{project_id}/confirm")
async def confirm_card(project_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    project = await session.get(Project, project_id)
    card = await session.scalar(
        select(ProductionCard).where(ProductionCard.project_id == project_id).order_by(desc(ProductionCard.version)).limit(1)
    )
    if project is None or card is None:
        raise HTTPException(status_code=409, detail="请先选择视频方向")
    card.status = "confirmed"
    current_script = await session.scalar(
        select(ScriptVersion)
        .where(ScriptVersion.production_card_id == card.id)
        .order_by(desc(ScriptVersion.version))
        .limit(1)
    )
    existing_run = await session.scalar(
        select(WorkflowRun)
        .where(
            WorkflowRun.project_id == project_id,
            WorkflowRun.workflow_type == "production",
            WorkflowRun.status.in_(["queued", "running"]),
        )
        .order_by(desc(WorkflowRun.created_at))
        .limit(1)
    )
    created_run = existing_run is None and current_script is None
    if created_run:
        existing_run = WorkflowRun(project_id=project_id, workflow_type="production")
        session.add(existing_run)
        await session.flush()
    project.stage = "script_ready" if current_script is not None else "script_production"
    await session.commit()
    if created_run:
        production_runner.submit(existing_run.id)
    return await _project_payload(session, project_id)


@app.post("/v1/projects/{project_id}/produce-media")
async def produce_media(project_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    project = await session.get(Project, project_id)
    card = await session.scalar(
        select(ProductionCard).where(ProductionCard.project_id == project_id).order_by(desc(ProductionCard.version)).limit(1)
    )
    script = await session.scalar(
        select(ScriptVersion).where(ScriptVersion.project_id == project_id).order_by(desc(ScriptVersion.version)).limit(1)
    )
    if project is None or card is None or card.status != "confirmed" or script is None:
        raise HTTPException(status_code=409, detail="请先确认制作卡并生成脚本")
    voice_script, build_version, storyboard = await _voice_plan(session, project_id)
    missing = [
        scene["index"]
        for scene in storyboard["scenes"]
        if not (
            settings.asset_root
            / f"projects/{project_id}/media/v{voice_script.version}/build-{build_version}/audio/scene-{scene['index'] + 1:02d}.mp3"
        ).is_file()
    ]
    if missing:
        raise HTTPException(status_code=409, detail=f"还有 {len(missing)} 个分镜没有从浏览器上传配音")
    active_run = await session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.project_id == project_id,
            WorkflowRun.workflow_type == "media_production",
            WorkflowRun.status.in_(["queued", "running"]),
        ).order_by(desc(WorkflowRun.created_at)).limit(1)
    )
    if active_run is None:
        active_run = WorkflowRun(project_id=project_id, workflow_type="media_production", step="waiting_for_script")
        session.add(active_run)
        project.stage = "media_production"
        await session.commit()
        media_runner.submit(active_run.id)
    return await _project_payload(session, project_id)


@app.patch("/v1/projects/{project_id}/production-card")
async def update_card(project_id: str, payload: UpdateProductionCardRequest, session: AsyncSession = Depends(get_session)) -> dict:
    card = await session.scalar(
        select(ProductionCard).where(ProductionCard.project_id == project_id).order_by(desc(ProductionCard.version)).limit(1)
    )
    if card is None:
        raise HTTPException(status_code=404, detail="制作卡不存在")
    if card.status == "confirmed":
        raise HTTPException(status_code=409, detail="制作卡已经锁定")
    if payload.title is not None:
        card.title = payload.title.strip()
    if payload.duration_seconds is not None:
        card.duration_seconds = payload.duration_seconds
    await session.commit()
    return await _project_payload(session, project_id)


@app.get("/v1/assets/{asset_id}")
async def get_asset(asset_id: str, session: AsyncSession = Depends(get_session)) -> FileResponse:
    asset = await session.get(MediaAsset, asset_id)
    if asset is None or asset.status != "ready":
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    try:
        path = media_runner.pipeline.assets.path_for_read(asset.relative_path)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="媒体文件不存在") from None
    media_types = {
        ".mp4": "video/mp4", ".mp3": "audio/mpeg", ".png": "image/png",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".ass": "text/plain; charset=utf-8",
    }
    return FileResponse(path, media_type=media_types.get(path.suffix.lower(), "application/octet-stream"))


@app.get("/v1/runs/{run_id}/events")
async def run_events(run_id: str, session: AsyncSession = Depends(get_session)) -> StreamingResponse:
    if await session.get(WorkflowRun, run_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def stream():
        cursor = 0
        while True:
            async with SessionLocal() as event_session:
                events = (await event_session.scalars(
                    select(WorkflowEvent).where(WorkflowEvent.run_id == run_id, WorkflowEvent.id > cursor).order_by(WorkflowEvent.id)
                )).all()
                for event in events:
                    cursor = event.id
                    yield f"id: {event.id}\nevent: {event.event_type}\ndata: {json.dumps({'message': event.message, **event.payload}, ensure_ascii=False)}\n\n"
                run = await event_session.get(WorkflowRun, run_id)
                if run and run.status in {"completed", "failed", "cancelled"} and not events:
                    break
            yield ": keepalive\n\n"
            await asyncio.sleep(0.8)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
