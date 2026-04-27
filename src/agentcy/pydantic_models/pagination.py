"""
Pagination and sorting models for list endpoints.

Provides backward-compatible pagination support where:
- Default behavior (no limit) returns all items (legacy)
- Specifying limit/offset enables paginated responses
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")


class SortOrder(str, Enum):
    """Sort direction for list queries."""

    ASC = "asc"
    DESC = "desc"


class PaginationParams(BaseModel):
    """
    Standard pagination parameters for list endpoints.

    Defaults to NO pagination when limit is None (backward compatible).
    """

    limit: Optional[int] = Field(
        default=None,
        ge=1,
        le=1000,
        description="Maximum number of items to return. None returns all (backward compatible).",
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of items to skip before starting to return results.",
    )

    @field_validator("limit", mode="before")
    @classmethod
    def empty_string_to_none(cls, v: Any) -> Any:
        """Allow empty string to mean 'no limit' for query param compatibility."""
        if v == "" or v == "null":
            return None
        return v


class SortParams(BaseModel):
    """Standard sorting parameters for list endpoints."""

    sort_by: Optional[str] = Field(
        default=None,
        description="Field name to sort by. Must be a valid field on the entity.",
    )
    sort_order: SortOrder = Field(
        default=SortOrder.DESC,
        description="Sort direction: 'asc' or 'desc'.",
    )


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Wrapper for paginated list responses.

    Provides metadata about the pagination state alongside the data.
    """

    items: List[T]
    total: int = Field(
        ..., description="Total number of items matching the query (before pagination)."
    )
    limit: Optional[int] = Field(
        None, description="The limit that was applied, or None if no limit."
    )
    offset: int = Field(0, description="The offset that was applied.")
    has_more: bool = Field(
        ..., description="True if there are more items after this page."
    )

    @classmethod
    def from_items(
        cls,
        items: List[T],
        total: int,
        limit: Optional[int],
        offset: int,
    ) -> "PaginatedResponse[T]":
        """Factory method to construct response with computed has_more."""
        has_more = (offset + len(items)) < total if limit else False
        return cls(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            has_more=has_more,
        )


# Convenience type aliases
PaginatedDict = PaginatedResponse[Dict[str, Any]]
