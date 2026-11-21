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

from services.gateway.trainer import alpaca, curriculum, facilitator, runner, selffix
from services.gateway.trainer import catalog as catalog_mod
from services.gateway.trainer import dream as dream_mod
from services.gateway.trainer import sermon as sermon_mod
from services.gateway.trainer.llm import LocalModelUnavailable
from services.gateway.workspace_api import WorkspaceError

log = logging.getLogger("gateway.trainer.routes")

router = APIRouter(prefix="/api/trainer")


class EvalRequest(BaseModel):
    buckets: list[str] | None = None
    limit: int = Field(default=12, ge=1, le=200)
    budget_s: float = Field(default=600.0, ge=10.0, le=3600.0)
    model: str | None = None


class RepairSpec(BaseModel):
    """A repair inside a cycle: the same bounds as ``POST /repair``."""

    workspace_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    error_text: str | None = None
    test_targets: list[str] = Field(default_factory=list)
    attempts: int = Field(default=2, ge=1, le=selffix.MAX_ATTEMPTS)


class CycleRequest(BaseModel):
    buckets: list[str] | None = None
    limit: int = Field(default=12, ge=1, le=200)
    budget_s: float = Field(default=600.0, ge=10.0, le=3600.0)
    apply_lessons: bool = True
    repair: RepairSpec | None = None


class RepairRequest(BaseModel):
    workspace_id: str
    path: str
    error_text: str | None = None
    test_targets: list[str] | None = None
    attempts: int = Field(default=2, ge=1, le=selffix.MAX_ATTEMPTS)


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


def _rag_http_error(exc: dream_mod.RagError) -> HTTPException:
    """RAG down is 503; RAG refusing the request is a 502 with its words."""
    status = 503 if isinstance(exc, dream_mod.RagUnavailable) else 502
    return HTTPException(status_code=status, detail=str(exc))


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


@router.get("/hardware")
async def get_hardware(request: Request) -> dict[str, Any]:
    """The machine that hosts the model: GPU, slots, loaded models, LoRA, verdict."""
    await _admin(request)
    from services.gateway.orchestrator import get_llm_settings

    return await alpaca.probe(await get_llm_settings())


@router.get("/models")
async def get_models(request: Request) -> dict[str, Any]:
    """Alpaca's model inventory, and which one the trainer would use."""
    await _admin(request)
    from services.gateway.orchestrator import get_llm_settings

    settings = await get_llm_settings()
    base = alpaca.proxy_base(settings)
    if not base:
        return {
            "total": 0,
            "models": [],
            "chosen": "",
            "trainer_model_setting": "",
            "trainer_model_missing": False,
            "reason": "llm_local_url is not set, so there is no model host to ask",
        }
    try:
        payload = await alpaca.get_json(f"{base}/admin/models")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"The model host did not list its models: {exc}") from exc
    return alpaca.summarise_models(payload, settings)


@router.get("/benchmarks")
async def get_benchmarks(request: Request, limit: int = 20) -> dict[str, Any]:
    """Alpaca's own benchmark results, and the categories worth teaching next."""
    await _admin(request)
    from services.gateway.orchestrator import get_llm_settings

    try:
        summary = alpaca.benchmark_summary(limit=max(1, min(limit, 100)))
    except alpaca.BenchmarksUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    settings = await get_llm_settings()
    known = [{"name": row["model"]} for row in summary["results"] if row.get("model")]
    model = alpaca.choose_model(settings, known)
    return {
        **summary,
        "chosen_model": model,
        "weak_categories": alpaca.weakest_categories(summary, model),
    }


@router.post("/benchmarks/ingest")
async def ingest_benchmarks(request: Request) -> dict[str, Any]:
    """Fold Alpaca's measured results into lesson confidence (RAG ingest-results)."""
    await _admin(request)
    try:
        return await dream_mod.ingest_benchmark_results()
    except dream_mod.RagError as exc:
        raise _rag_http_error(exc) from exc


@router.post("/eval")
async def run_eval(request: Request, body: EvalRequest) -> dict[str, Any]:
    """Run a bounded slice of the curriculum on the local model and keep the report."""
    await _admin(request)
    cases = curriculum.derive_curriculum(
        catalog_mod.build_catalog(request.app), buckets=body.buckets, limit=body.limit
    )
    try:
        # body.model, when given, is the model every case asks (validated against
        # the host's inventory first); otherwise the trainer's own choice.
        return await runner.run_curriculum(
            cases, model=body.model, budget_s=body.budget_s
        )
    except LocalModelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except runner.TrainerStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/reports")
async def list_reports(request: Request, limit: int = 20) -> dict[str, Any]:
    """The most recent evaluation reports, newest first."""
    await _admin(request)
    return {"reports": runner.list_reports(limit=max(1, min(limit, 100)))}


def _load_report_or_error(report_id: str) -> dict[str, Any]:
    try:
        report = runner.load_report(report_id)
    except runner.TrainerStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.get("/reports/{report_id}")
async def get_report(report_id: str, request: Request) -> dict[str, Any]:
    """One evaluation report, with every case's answer and failed checks."""
    await _admin(request)
    return _load_report_or_error(report_id)


@router.post("/reports/dataset")
async def export_dataset(request: Request, body: ExportRequest) -> dict[str, Any]:
    """Write a report's rows as JSONL, ready for a fine-tuning pass."""
    await _admin(request)
    report = _load_report_or_error(body.report_id)
    try:
        return runner.export_dataset(report, accepted_only=body.accepted_only)
    except runner.TrainerStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    except WorkspaceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
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
    except sermon_mod.ExecutionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/dream")
async def run_dream(request: Request, body: DreamRequest, user_id: str = "default") -> dict[str, Any]:
    """Run the dreaming passes, and return what they imply for the backlog."""
    await _admin(request)
    try:
        report = await dream_mod.dream(user_id, compact_at=body.compact_at, summary_len=body.summary_len)
    except dream_mod.RagError as exc:
        raise _rag_http_error(exc) from exc
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
    except dream_mod.RagError as exc:
        raise _rag_http_error(exc) from exc


@router.get("/backlog")
async def get_backlog(request: Request) -> dict[str, Any]:
    """The proposals from the last facilitator cycle."""
    await _admin(request)
    try:
        return facilitator.read_backlog()
    except runner.TrainerStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/cycle")
async def run_cycle(request: Request, body: CycleRequest) -> dict[str, Any]:
    """Dream, evaluate, propose, remember (and optionally repair) in one pass."""
    user = await _admin(request)
    try:
        return await facilitator.cycle(
            user=user,
            buckets=body.buckets,
            limit=body.limit,
            budget_s=body.budget_s,
            apply_lessons=body.apply_lessons,
            repair=body.repair.model_dump() if body.repair else None,
        )
    except facilitator.CycleBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except runner.TrainerStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
