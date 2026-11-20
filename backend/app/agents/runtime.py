"""Agent runtime: structured agents with deterministic fallbacks."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from app.agents import heuristics, prompts
from app.agents.costing import estimate_cost
from app.agents.heuristics import AgentContext
from app.agents.schemas import (
    ArchitectureReview,
    ArchitectureSpec,
    ClarificationQuestion,
    PlanSummary,
    Recommendation,
    RequirementAnalysis,
    RequirementItem,
    TerraformRepairResult,
)
from app.agents.validation import ArchitectureValidationError, normalize_and_validate
from app.config import get_settings
from app.llm.base import LLMError
from app.llm.factory import get_llm

T = TypeVar("T")


@dataclass
class AgentResult(Generic[T]):
    output: T
    provider: str = "offline"
    model: str = "deterministic-heuristics"
    latency_ms: int = 0
    used_llm: bool = False
    notes: list[str] = field(default_factory=list)


def _llm_meta(provider, result) -> dict[str, object]:
    return {
        "provider": result.provider if result else provider.name,
        "model": result.model if result else provider.model,
        "latency_ms": result.latency_ms if result else 0,
    }


class RequirementAnalyst:
    name = "requirement_analyst"

    def run(self, ctx: AgentContext) -> AgentResult[RequirementAnalysis]:
        llm = get_llm()
        started = time.perf_counter()
        if llm.available:
            try:
                analysis, result = llm.complete_json(
                    prompts.REQUIREMENT_ANALYST_SYSTEM,
                    self._prompt(ctx),
                    RequirementAnalysis,
                    max_tokens=4000,
                )
                analysis = self._postprocess(analysis, ctx)
                return AgentResult(
                    output=analysis,
                    provider=result.provider,
                    model=result.model,
                    latency_ms=result.latency_ms,
                    used_llm=True,
                )
            except LLMError as exc:
                analysis = heuristics.analyze_requirements(ctx)
                return AgentResult(
                    output=analysis,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    notes=[f"LLM requirement analysis failed, used deterministic reasoner: {exc}"],
                )
        analysis = heuristics.analyze_requirements(ctx)
        return AgentResult(
            output=analysis,
            latency_ms=int((time.perf_counter() - started) * 1000),
            notes=["Deterministic reasoner (no LLM provider configured)."],
        )

    def _prompt(self, ctx: AgentContext) -> str:
        messages = "\n".join(f"- {m}" for m in ctx.messages[-16:])
        requirements = prompts.json_block(ctx.requirements, 12000)
        docs = "\n".join(
            prompts.fence(d.get("filename", "document"), str(d.get("extracted_text", ""))[:6000])
            for d in ctx.documents[:5]
        )
        answered = prompts.json_block(ctx.answered_questions, 6000)
        return (
            f"Project: {ctx.project_name}\nEnvironment: {ctx.environment}\nRegion: {ctx.region}\n"
            f"Description: {ctx.description}\n\n"
            f"Conversation messages:\n{messages}\n\n"
            f"Existing structured requirements (JSON):\n{requirements}\n\n"
            f"Previously answered questions (JSON):\n{answered}\n\n"
            f"Uploaded documents:\n{docs or '(none)'}\n\n"
            "Analyse the requirements. Return the full updated structured requirement analysis. "
            "If the request is clear and complete, return an empty questions list and set "
            "requirements_complete true. Prioritise mandatory gaps first."
        )

    def _postprocess(self, analysis: RequirementAnalysis, ctx: AgentContext) -> RequirementAnalysis:
        seen = {r.get("description", "").strip().lower() for r in ctx.requirements}
        merged: list[RequirementItem] = list(analysis.requirements)
        for item in heuristics.extract_requirements(ctx):
            if item.description.strip().lower() not in seen:
                merged.append(item)
        deduped: list[RequirementItem] = []
        keys: set[str] = set()
        for item in merged:
            key = item.description.strip().lower()
            if key and key not in keys:
                keys.add(key)
                deduped.append(item)
        analysis.requirements = deduped[:80]

        questions: list[ClarificationQuestion] = []
        qids: set[str] = set()
        for index, question in enumerate(analysis.questions[:4], start=1):
            qid = question.id if question.id.startswith("q-") else f"q-{index}"
            if qid in qids:
                qid = f"{qid}-{index}"
            qids.add(qid)
            question.id = qid
            questions.append(question)
        analysis.questions = questions
        return analysis


class AzureSolutionArchitect:
    name = "azure_solution_architect"

    def run(self, ctx: AgentContext, analysis: RequirementAnalysis) -> AgentResult[ArchitectureSpec]:
        llm = get_llm()
        started = time.perf_counter()
        if llm.available:
            try:
                spec, result = llm.complete_json(
                    prompts.ARCHITECT_SYSTEM,
                    self._prompt(ctx, analysis),
                    ArchitectureSpec,
                    max_tokens=8000,
                )
                outcome = normalize_and_validate(spec, environment=ctx.environment)
                return AgentResult(
                    output=outcome.spec,
                    provider=result.provider,
                    model=result.model,
                    latency_ms=result.latency_ms,
                    used_llm=True,
                    notes=outcome.warnings,
                )
            except (LLMError, ArchitectureValidationError) as exc:
                spec = heuristics.design_architecture(ctx, analysis)
                outcome = normalize_and_validate(spec, environment=ctx.environment)
                return AgentResult(
                    output=outcome.spec,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    notes=[
                        f"LLM architecture was unusable ({exc}); used the deterministic architect.",
                        *outcome.warnings,
                    ],
                )
        spec = heuristics.design_architecture(ctx, analysis)
        outcome = normalize_and_validate(spec, environment=ctx.environment)
        return AgentResult(
            output=outcome.spec,
            latency_ms=int((time.perf_counter() - started) * 1000),
            notes=["Deterministic architect (no LLM provider configured).", *outcome.warnings],
        )

    def _prompt(self, ctx: AgentContext, analysis: RequirementAnalysis) -> str:
        return (
            f"Project: {ctx.project_name}\nEnvironment: {ctx.environment}\n"
            f"Preferred region: {ctx.region}\nDescription: {ctx.description}\n\n"
            f"Confirmed requirements (JSON):\n{prompts.json_block(analysis.requirements, 20000)}\n\n"
            f"Missing information:\n{analysis.missing_information}\n"
            f"Conflicts:\n{analysis.conflicts}\n"
            f"Assumptions:\n{analysis.assumptions}\n\n"
            "Design the complete Azure architecture for this project. "
            "Include a resource inventory, VNet/subnet plan, security controls, availability/DR, "
            "cost SKUs, recommendations, explicit assumptions and decisions with rationale."
        )


class ArchitectureReviewer:
    name = "architecture_reviewer"

    def run(self, spec: ArchitectureSpec, environment: str) -> AgentResult[ArchitectureReview]:
        baseline = heuristics.review_architecture(spec, environment)
        llm = get_llm()
        started = time.perf_counter()
        if llm.available:
            try:
                review, result = llm.complete_json(
                    prompts.REVIEWER_SYSTEM,
                    self._prompt(spec, environment),
                    ArchitectureReview,
                    max_tokens=5000,
                )
                review = self._merge(baseline, review)
                return AgentResult(
                    output=review,
                    provider=result.provider,
                    model=result.model,
                    latency_ms=result.latency_ms,
                    used_llm=True,
                )
            except LLMError as exc:
                return AgentResult(
                    output=baseline,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    notes=[f"LLM review failed, used deterministic review: {exc}"],
                )
        return AgentResult(
            output=baseline,
            latency_ms=int((time.perf_counter() - started) * 1000),
            notes=["Deterministic review (no LLM provider configured)."],
        )

    def _prompt(self, spec: ArchitectureSpec, environment: str) -> str:
        return (
            f"Environment: {environment}\nArchitecture JSON:\n"
            f"{prompts.json_block(spec.model_dump(mode='json'), 50000)}\n\n"
            "Review this architecture and return recommendations. Do not modify the architecture."
        )

    def _merge(self, baseline: ArchitectureReview, review: ArchitectureReview) -> ArchitectureReview:
        combined: list[Recommendation] = []
        seen: set[str] = set()
        # Mandatory deterministic findings always survive.
        for rec in [*baseline.recommendations, *review.recommendations]:
            key = rec.recommendation.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            rec.id = f"rec-{len(combined) + 1}"
            combined.append(rec)
        review.recommendations = combined
        review.score = min(baseline.score, review.score) if review.score else baseline.score
        if not review.risks:
            review.risks = baseline.risks
        if not review.overall_assessment:
            review.overall_assessment = baseline.overall_assessment
        return review


class PlanAnalyst:
    name = "terraform_plan_analyst"

    def run(self, summary: PlanSummary) -> AgentResult[PlanSummary]:
        llm = get_llm()
        started = time.perf_counter()
        deterministic = self._narrative(summary)
        if not llm.available:
            summary.narrative = deterministic
            return AgentResult(
                output=summary,
                latency_ms=int((time.perf_counter() - started) * 1000),
                notes=["Deterministic plan narrative (no LLM provider configured)."],
            )
        try:
            result = llm.complete(
                prompts.PLAN_ANALYST_SYSTEM,
                "Plan summary JSON:\n"
                + prompts.json_block(summary.model_dump(mode="json"), 30000)
                + "\n\nExplain the plan, risks and required approvals in under 250 words.",
                max_tokens=1200,
            )
            narrative = result.text.strip()
            if len(narrative) < 20:
                narrative = deterministic
            summary.narrative = narrative
            return AgentResult(
                output=summary,
                provider=result.provider,
                model=result.model,
                latency_ms=result.latency_ms,
                used_llm=True,
            )
        except LLMError:
            summary.narrative = deterministic
            return AgentResult(
                output=summary,
                latency_ms=int((time.perf_counter() - started) * 1000),
                notes=["LLM plan explanation failed; used deterministic narrative."],
            )

    def _narrative(self, summary: PlanSummary) -> str:
        if summary.total == 0:
            return "Terraform reports no changes. The deployed infrastructure already matches the approved configuration."
        parts = [
            f"Terraform plans {summary.add} to add, {summary.change} to change, "
            f"{summary.destroy} to destroy and {summary.replace} to replace "
            f"({summary.total} total)."
        ]
        if summary.destructive_operations:
            parts.append(
                "DESTRUCTIVE OPERATIONS DETECTED: "
                + "; ".join(
                    f"{op.address} will be {op.action} ({op.severity})."
                    for op in summary.destructive_operations[:6]
                )
                + " Review data-loss and downtime impact before approving."
            )
        else:
            parts.append(
                "No destructive operations are planned. That does not by itself prove the plan is safe: "
                "review the resource list, security rules and target subscription."
            )
        if summary.outputs:
            parts.append(
                "Outputs will be updated: " + ", ".join(sorted(summary.outputs.keys()))
                + ". Sensitive values are not displayed."
            )
        return " ".join(parts)


class TerraformEngineer:
    """Bounded repair agent for validation failures.

    It only proposes changes to existing `.tf` files. The deterministic validator
    and policy engine still decide whether the result is acceptable; nothing the
    model writes is trusted.
    """

    name = "terraform_engineer"
    MAX_FILES = 12
    MAX_CHARS = 120_000
    FORBIDDEN_SNIPPETS = ("local-exec", "remote-exec", "external data", "null_resource")

    def repair(
        self,
        files: dict[str, str],
        *,
        errors: str,
        environment: str,
    ) -> AgentResult[dict[str, str]] | None:
        llm = get_llm()
        if not llm.available:
            return None
        candidates = {path: content for path, content in files.items() if path.endswith(".tf")}
        selected: dict[str, str] = {}
        used = 0
        for path in sorted(candidates):
            content = candidates[path]
            if used + len(content) > self.MAX_CHARS:
                break
            selected[path] = content
            used += len(content)
            if len(selected) >= self.MAX_FILES:
                break
        if not selected:
            return None
        prompt = (
            f"Environment: {environment}\n\n"
            "Validation errors:\n"
            f"{errors[:12000]}\n\n"
            "Existing Terraform files (path followed by content):\n"
            + "\n\n".join(f"### FILE: {path}\n{content}" for path, content in selected.items())
            + "\n\nReturn only the files that must change. Keep the architecture intent, provider pins, "
            "security controls and module interfaces intact. Do not add provisioners, external data "
            "sources or credentials."
        )
        try:
            result, meta = llm.complete_json(
                prompts.TERRAFORM_FIX_SYSTEM,
                prompt,
                TerraformRepairResult,
                max_tokens=12000,
            )
        except LLMError as exc:
            return AgentResult(
                output={},
                used_llm=True,
                notes=[f"Repair model failed: {exc}"],
            )

        merged = dict(files)
        changed = 0
        notes: list[str] = []
        for patch in result.files:
            path = patch.path.replace("\\", "/").strip("/")
            if path not in candidates:
                notes.append(f"Ignored repair for unknown file: {path}")
                continue
            if any(snippet in patch.content.lower() for snippet in self.FORBIDDEN_SNIPPETS):
                notes.append(f"Rejected unsafe repair for {path}")
                continue
            if patch.content.strip() and patch.content != candidates[path]:
                merged[path] = patch.content
                changed += 1
            elif not patch.content.strip():
                notes.append(f"Rejected empty repair for {path}")
        if not changed:
            return AgentResult(
                output=merged,
                provider=meta.provider,
                model=meta.model,
                latency_ms=meta.latency_ms,
                used_llm=True,
                notes=[*notes, "Model returned no usable changes."],
            )
        return AgentResult(
            output=merged,
            provider=meta.provider,
            model=meta.model,
            latency_ms=meta.latency_ms,
            used_llm=True,
            notes=[*notes, f"Repaired {changed} file(s): {result.summary}"[:1000]],
        )


requirement_analyst = RequirementAnalyst()
azure_solution_architect = AzureSolutionArchitect()
architecture_reviewer = ArchitectureReviewer()
plan_analyst = PlanAnalyst()
terraform_engineer = TerraformEngineer()
