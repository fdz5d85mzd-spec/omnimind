"""Distributed fleet (M8) — leader-elected orchestrator, health-based
rebalancing, pluggable Postgres-backed storage, and a real-time event bus."""

from omni.fleet.bus import FleetBus, InMemoryBus, NatsBus, Subscription, make_bus
from omni.fleet.node import FleetNode
from omni.fleet.protocol import (
    LeaderInfo,
    NodeAnnouncement,
    NodeHealth,
    NodeRole,
    WorkloadStats,
)
from omni.fleet.storage import FleetStorage, PostgresFleetStorage

__all__ = [
    "FleetNode",
    "LeaderInfo",
    "NodeAnnouncement",
    "NodeHealth",
    "NodeRole",
    "WorkloadStats",
    "FleetStorage",
    "PostgresFleetStorage",
    "FleetBus",
    "InMemoryBus",
    "NatsBus",
    "Subscription",
    "make_bus",
]
