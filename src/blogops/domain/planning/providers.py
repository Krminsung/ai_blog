"""Provider boundary for AI-assisted monthly calendar proposals."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from blogops.domain.planning.rules import idea_duplicate_key
from blogops.domain.planning.schemas import MonthlyPlanProposalCreate


@dataclass(frozen=True, slots=True)
class GeneratedMonthlyPlan:
    provider: str
    provider_version: str
    items: list[dict[str, Any]]
    generation_metadata: dict[str, Any]


class MonthlyPlanGenerator(Protocol):
    async def generate(
        self,
        request: MonthlyPlanProposalCreate,
        *,
        generation_policy: dict[str, Any],
    ) -> GeneratedMonthlyPlan: ...


class DeterministicMonthlyPlanGenerator:
    """Safe local fallback; production can inject an external structured-output AI adapter."""

    async def generate(
        self,
        request: MonthlyPlanProposalCreate,
        *,
        generation_policy: dict[str, Any],
    ) -> GeneratedMonthlyPlan:
        unique: dict[str, Any] = {}
        for seed in request.seeds:
            key = idea_duplicate_key(
                title=seed.topic,
                intent=seed.search_intent.value,
                keyword_cluster_id=seed.keyword_cluster_id,
                semantic_group_key=seed.semantic_group_key,
                primary_keyword=seed.topic,
            )
            unique.setdefault(key, seed)
        seeds = list(unique.values())
        timezone = ZoneInfo(request.timezone)
        days_in_month = monthrange(request.month.year, request.month.month)[1]
        configured_channels = generation_policy.get("allowed_channels", [])
        default_channel = (
            str(configured_channels[0]) if isinstance(configured_channels, list) and configured_channels else "BLOG"
        )
        spacing = max(days_in_month // max(len(seeds), 1), 1)
        items: list[dict[str, Any]] = []
        for index, seed in enumerate(seeds):
            day = min(1 + index * spacing, days_in_month)
            scheduled = datetime.combine(
                request.month.replace(day=day), time(hour=10), tzinfo=timezone
            )
            items.append(
                {
                    "title": seed.topic,
                    "scheduled_at": scheduled.isoformat(),
                    "timezone": request.timezone,
                    "channel": seed.preferred_channel or default_channel,
                    "language": "ko-KR",
                    "brief_id": None,
                    "primary_keyword_id": (
                        str(seed.primary_keyword_id) if seed.primary_keyword_id else None
                    ),
                    "keyword_cluster_id": (
                        str(seed.keyword_cluster_id) if seed.keyword_cluster_id else None
                    ),
                    "semantic_group_key": seed.semantic_group_key,
                    "search_intent": seed.search_intent.value,
                    "journey_stage": seed.journey_stage.value,
                    "assignee_user_ids": [],
                }
            )
        return GeneratedMonthlyPlan(
            provider="deterministic-fallback",
            provider_version="1",
            items=items,
            generation_metadata={
                "input_count": len(request.seeds),
                "deduplicated_count": len(seeds),
                "suppressed_count": len(request.seeds) - len(seeds),
                "generated_at": (
                    datetime.now(tz=timezone) + timedelta(seconds=0)
                ).isoformat(),
            },
        )
