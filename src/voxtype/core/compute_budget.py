"""Resource limits for the existing local inference workers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class PerformanceProfile(StrEnum):
    ECO = "energy"
    BALANCED = "balanced"
    PERFORMANCE = "maximum"

    @classmethod
    def resolve(cls, value: str) -> PerformanceProfile:
        mode = value.strip().casefold()
        if mode == "automatic":
            from voxtype.windows.power_events import on_battery_power

            return cls.ECO if on_battery_power() is True else cls.BALANCED
        if mode in {"energy", "energy_saving", "eco"}:
            return cls.ECO
        if mode in {"maximum", "performance", "max_performance"}:
            return cls.PERFORMANCE
        return cls.BALANCED


class ResourcePhase(StrEnum):
    COLD = "cold"
    INITIALIZING = "initializing"
    READY = "ready"
    ACTIVE = "active"
    QUIESCING = "quiescing"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


class DecodingPreset(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    ACCURATE = "accurate"


class MemoryPolicy(StrEnum):
    KEEP_LOADED = "keep_loaded"
    UNLOAD_AFTER_IDLE = "unload_after_idle"
    EXPLICIT = "explicit"


@dataclass(frozen=True, slots=True)
class ComputeBudget:
    asr_intra_threads: int
    asr_inter_threads: int = 1
    vad_intra_threads: int = 1
    vad_inter_threads: int = 1

    @classmethod
    def from_settings(cls, power_mode: str, requested_threads: int = 0) -> ComputeBudget:
        profile = PerformanceProfile.resolve(power_mode)
        default_threads = {
            PerformanceProfile.ECO: 2,
            PerformanceProfile.BALANCED: 4,
            PerformanceProfile.PERFORMANCE: 6,
        }[profile]
        logical_threads = os.cpu_count() or 2
        return cls(
            min(logical_threads, requested_threads if requested_threads > 0 else default_threads)
        )
