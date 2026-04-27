"""
Aggregation response models for Graph Store statistics endpoints.

Provides structured responses for analytics queries including:
- Bid score statistics (avg, min, max, stddev)
- Status counts grouped by status field
- Entity counts across all entity types
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class BidScoreStats(BaseModel):
    """Aggregated statistics for bid scores."""

    count: int = Field(..., description="Total number of bids.")
    avg_score: Optional[float] = Field(None, description="Average bid score.")
    min_score: Optional[float] = Field(None, description="Minimum bid score.")
    max_score: Optional[float] = Field(None, description="Maximum bid score.")
    stddev_score: Optional[float] = Field(
        None, description="Standard deviation of bid scores."
    )


class StatusCount(BaseModel):
    """Count of items grouped by status."""

    status: str = Field(..., description="Status value.")
    count: int = Field(..., description="Number of items with this status.")


class EntityCounts(BaseModel):
    """Counts for various entity types for a user."""

    task_specs: int = Field(default=0, description="Number of task specifications.")
    bids: int = Field(default=0, description="Number of blueprint bids.")
    plan_drafts: int = Field(default=0, description="Number of plan drafts.")
    plan_revisions: int = Field(default=0, description="Number of plan revisions.")
    plan_suggestions: int = Field(default=0, description="Number of plan suggestions.")
    cfps: int = Field(default=0, description="Number of calls for proposals.")
    awards: int = Field(default=0, description="Number of contract awards.")
    human_approvals: int = Field(default=0, description="Number of human approvals.")
    ethics_checks: int = Field(default=0, description="Number of ethics checks.")
    strategy_plans: int = Field(default=0, description="Number of strategy plans.")
    execution_reports: int = Field(default=0, description="Number of execution reports.")
    audit_logs: int = Field(default=0, description="Number of audit log entries.")
    escalations: int = Field(default=0, description="Number of escalation notices.")
    affordance_markers: int = Field(default=0, description="Number of affordance markers.")
    reservation_markers: int = Field(default=0, description="Number of reservation markers.")


class GraphStoreStats(BaseModel):
    """Comprehensive statistics for the graph store."""

    username: str = Field(..., description="Username for which stats are computed.")
    entity_counts: EntityCounts = Field(
        ..., description="Counts of each entity type."
    )
    bid_stats: Optional[BidScoreStats] = Field(
        None, description="Bid score statistics (if bids exist)."
    )
    cfp_status_counts: List[StatusCount] = Field(
        default_factory=list, description="CFP counts grouped by status."
    )
    plan_suggestion_status_counts: List[StatusCount] = Field(
        default_factory=list, description="Plan suggestion counts grouped by status."
    )
