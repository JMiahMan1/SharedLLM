"""``/api/trainer`` -- the Lab's Training tab, and the trainer's control surface.

Route policy follows the rest of the gateway:

* reading the catalogue, the curriculum and the backlog needs a signed-in user
  (the data describes the household's own system, not anyone's private data);
* running an evaluation, exporting a dataset, dreaming, repairing a workspace or
  running a full cycle needs an admin, because each of them spends the local
  model's time or writes to a workspace;
* drafting a sermon or editing an image needs only a signed-in user, because
  writing into a workspace is already gated by that workspace's access policy.

The admin guard is imported from ``main`` inside the handlers on purpose: this
router is included by ``main``, so a top-level import would be a cycle.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from services.gateway.trainer import catalog as catalog_mod
from services.gateway.trainer import curriculum, facilitator, runner, selffix
from services.gateway.trainer import dream as dream_mod
from services.gateway.trainer import sermon as sermon_mod
from services.gateway.trainer.llm import LocalModelUnavailable

log = logging.getLogger("gateway.trainer.routes")

router = APIRouter(prefix="/api/trainer")


class EvalRequest(BaseModel):
    buckets: list[str] | None = None
    limit: int = Field(default=12, ge=1, le=200)
    budget_s: float = Field(default=600.0, ge=10.0, le=3600.0)
    model: str | None = None


class CycleRequest(BaseModel):
    buckets: list[str] | None = None
    limit: int = Field(default=12, ge=1, le=200)
    budget_s: float = Field(default=600.0, ge=10.0, le=3600.0)
    apply_lessons: bool = True
    repair: dict[str, Any] | None = None


class RepairRequest(BaseModel):
    workspace_id: str
    path: str
    error_text: str | None = None
    test_targets: list[str] | None = None
    attempts: int = Field(default=2, ge=1, le=3)


class SermonRequest(BaseModel):
    workspace_id: str
    passage: str
    title: str = ""
    theme: str = ""
    translation: str = "kjv"
    audience: str = "a Sunday congregation"
    tone: str = "warm and expository"
    points: int = Field(default=3, ge=1, le=6)
    series: str = ""
    save: bool = True


class IllustrateRequest(BaseModel):
    workspace_id: str
    image_path: str
    prompt: str
    output_path: str | None = None
    model: str | None = None


class ExportRequest(BaseModel):
    report_id: str
    accepted_only: bool = True


class DreamRequest(BaseModel):
    compact_at: int = Field(default=600, ge=200, le=4000)
    summary_len: int = Field(default=400, ge=100, le=2000)


class ValidateLessonRequest(BaseModel):
    lesson_id: str
    passed: bool
    evidence: str = ""


async def _admin(request: Request) -> str:
    from services.gateway.main import _require_admin

    return await _require_admin(request)


async def _user(request: Request) -> str:
    from services.gateway.main import _require_authenticated

    return await _require_authenticated(request)


@router.get("/catalog")
async def get_catalog(request: Request) -> dict[str, Any]:
    """Every capability Jarvis has, derived from the code (routes, tools, screens)."""
    await _user(request)
    return catalog_mod.build_catalog(request.app)


@router.get("/curriculum")
async def get_curriculum(request: Request, buckets: str | None = None, limit: int = 200) -> dict[str, Any]:
    """The derived evaluation/training cases, optionally narrowed to buckets."""
    await _user(request)
    wanted = [item.strip() for item in (buckets or "").split(",") if item.strip()] or None
    cases = curriculum.derive_curriculum(
        catalog_mod.build_catalog(request.app), buckets=wanted, limit=max(1, min(limit, 500))
    )
    return {
        "summary": curriculum.summarise(cases),
        "buckets": list(curriculum.BUCKETS),
        "cases": [case.to_dict() for case in cases],
    }


@router.post("/eval")
async def run_eval(request: Request, body: EvalRequest) -> dict[str, Any]:
    """Run a bounded slice of the curriculum on the local model and keep the report."""
    await _admin(request)
    cases = curriculum.derive_curriculum(
        catalog_mod.build_catalog(request.app), buckets=body.buckets, limit=body.limit
    )
    try:
        return await runner.run_curriculum(
            cases, model=body.model, budget_s=body.budget_s
        )
    except LocalModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/reports")
async def list_reports(request: Request, limit: int = 20) -> dict[str, Any]:
    """The most recent evaluation reports, newest first."""
    await _admin(request)
    return {"reports": runner.list_reports(limit=max(1, min(limit, 100)))}


@router.get("/reports/{report_id}")
async def get_report(report_id: str, request: Request) -> dict[str, Any]:
    """One evaluation report, with every case's answer and failed checks."""
    await _admin(request)
    report = runner.load_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.post("/reports/dataset")
async def export_dataset(request: Request, body: ExportRequest) -> dict[str, Any]:
    """Write a report's rows as JSONL, ready for a fine-tuning pass."""
    await _admin(request)
    report = runner.load_report(body.report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return runner.export_dataset(report, accepted_only=body.accepted_only)


@router.post("/repair")
async def repair(request: Request, body: RepairRequest) -> dict[str, Any]:
    """Ask the local model to fix a workspace file, then prove it with the tests."""
    user = await _admin(request)
    try:
        result = await selffix.repair_workspace(
            body.workspace_id,
            path=body.path,
            error_text=body.error_text,
            test_targets=body.test_targets,
            attempts=body.attempts,
            user=user,
        )
    except LocalModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return result.to_dict()


@router.post("/sermon")
async def compose_sermon(request: Request, body: SermonRequest) -> dict[str, Any]:
    """Draft a sermon from a passage and save it into the workspace."""
    user = await _user(request)
    try:
        drafted = await sermon_mod.compose_sermon(
            body.workspace_id,
            passage=body.passage,
            title=body.title,
            theme=body.theme,
            translation=body.translation,
            audience=body.audience,
            tone=body.tone,
            points=body.points,
            series=body.series,
            user=user,
            save=body.save,
        )
    except sermon_mod.SermonError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LocalModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return drafted.to_dict()


@router.post("/illustrate")
async def illustrate(request: Request, body: IllustrateRequest) -> dict[str, Any]:
    """Edit an image in the workspace (a sermon illustration, a series header)."""
    user = await _user(request)
    try:
        return await sermon_mod.illustrate(
            body.workspace_id,
            image_path=body.image_path,
            prompt=body.prompt,
            output_path=body.output_path,
            model=body.model,
            user=user,
        )
    except sermon_mod.SermonError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dream")
async def run_dream(request: Request, body: DreamRequest, user_id: str = "default") -> dict[str, Any]:
    """Run the dreaming passes, and return what they imply for the backlog."""
    await _admin(request)
    try:
        report = await dream_mod.dream(user_id, compact_at=body.compact_at, summary_len=body.summary_len)
    except dream_mod.RagUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "dream": report,
        "proposals": dream_mod.proposals_from_dream(report),
    }


@router.post("/dream/validate")
async def validate_lesson(request: Request, body: ValidateLessonRequest, user_id: str = "default") -> dict[str, Any]:
    """Report that a lesson was tested, so dreaming can weight it."""
    await _admin(request)
    try:
        return await dream_mod.validate(body.lesson_id, body.passed, body.evidence, user_id=user_id)
    except dream_mod.RagUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/backlog")
async def get_backlog(request: Request) -> dict[str, Any]:
    """The proposals from the last facilitator cycle."""
    await _admin(request)
    return facilitator.read_backlog()


@router.post("/cycle")
async def run_cycle(request: Request, body: CycleRequest) -> dict[str, Any]:
    """Dream, evaluate, propose, remember (and optionally repair) in one pass."""
    user = await _admin(request)
    return await facilitator.cycle(
        user=user,
        buckets=body.buckets,
        limit=body.limit,
        budget_s=body.budget_s,
        apply_lessons=body.apply_lessons,
        repair=body.repair,
    )
