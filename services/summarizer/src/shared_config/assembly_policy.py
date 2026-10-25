"""Assembly's registry policy — the task 1.8 single sources in domains.json.

The §9 Moments bands, the component and domain sets assembly keys on with its
thresholds, the measure words of a count label, and the tabs assembly adds on
its own. Each reader names the note in domains.json that explains it.
"""

from __future__ import annotations

import copy
from typing import TypedDict

from .domain_config import get_config


class MomentBand(TypedDict):
    """One ``momentCapsByDuration`` band — see ``momentCapsNote``."""

    maxMinutes: float | None
    minutesPerMoment: int
    min: int
    max: int


class KeyIdeasBand(TypedDict):
    """content-model §9's key-ideas count up to ``maxMinutes`` (None = no bound)."""

    maxMinutes: float | None
    count: int


class AssemblyPolicy(TypedDict):
    """``assemblyPolicy`` — see ``assemblyPolicyNote`` in domains.json."""

    sequentialComponents: list[str]
    stepListDomains: list[str]
    stepTimelinePrimaries: list[str]
    conceptPromotionDomains: list[str]
    noFilmstripDomains: list[str]
    sparseItemsMax: int
    denseItemsMin: int
    keyIdeasByDuration: list[KeyIdeasBand]
    keyIdeasCapFactor: int


def moment_caps_by_duration() -> list[MomentBand]:
    """The §9 Moments bands, shortest first — see ``momentCapsNote``."""
    return copy.deepcopy(get_config()["momentCapsByDuration"])


def assembly_policy() -> AssemblyPolicy:
    """Assembly's component and domain sets and thresholds — see ``assemblyPolicyNote``."""
    return copy.deepcopy(get_config()["assemblyPolicy"])


def measure_units() -> frozenset[str]:
    """Words that make a label's leading number a measure — see ``measureUnitsNote``."""
    return frozenset(get_config()["measureUnits"])


class InjectedTab(TypedDict):
    """One ``injectedTabs`` entry — see ``injectedTabsNote`` in domains.json."""

    label: str
    emoji: str
    goal: str


def injected_tab(name: str) -> InjectedTab:
    """The label, emoji and goal of a tab assembly adds on its own (``overview``,
    ``keyInfo``, ``framesGallery``, ``moments``) — see ``injectedTabsNote``."""
    return copy.deepcopy(get_config()["injectedTabs"][name])
