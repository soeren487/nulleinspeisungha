"""Tests for what Houses tell the DTU supervisors."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from custom_components.nulleinspeisung.const import SIGN_IMPORT
from custom_components.nulleinspeisung.dtu_models import DtuSnapshot, InverterSnapshot
from custom_components.nulleinspeisung.house import HouseConfig
from custom_components.nulleinspeisung.house_knowledge import (
    MIN_PRODUCING_POWER,
    HouseKnowledge,
)


def inverter(
    serial: str,
    power: float | None = 200.0,
    producing: bool = True,
    data_age: int = 3,
) -> InverterSnapshot:
    """An Inverter snapshot."""
    return InverterSnapshot(
        serial=serial,
        name=serial,
        reachable=True,
        producing=producing,
        poll_enabled=True,
        data_age=data_age,
        power=power,
        limit=100.0,
        limit_set_status="Ok",
        rated_power=600,
        model=None,
    )


def dtu(*inverters: InverterSnapshot, ok: bool = True, staleness: float = 120) -> Any:
    """A stand-in for a DTU coordinator."""
    snapshot = DtuSnapshot(
        hostname="h",
        firmware_version="v",
        chip_model="c",
        uptime=1,
        inverters={i.serial: i for i in inverters},
    )
    return SimpleNamespace(
        data=snapshot, last_update_success=ok, staleness_time=staleness
    )


def house(name: str, inverters: list[str], bb: list[str] | None = None) -> HouseConfig:
    """A House configuration."""
    return HouseConfig(
        subentry_id=name,
        unique_id=name,
        name=name,
        latitude=0.0,
        longitude=0.0,
        grid_meter="sensor.grid_meter",
        grid_meter_sign=SIGN_IMPORT,
        inverters=tuple(inverters),
        battery_backed=tuple(bb or []),
    )


def test_battery_backed_lookup() -> None:
    """An Inverter is Battery-backed if any House marks it so."""
    knowledge = HouseKnowledge(
        [house("a", ["x", "y"], ["x"]), house("b", ["z", "w"], ["w"])], {}
    )
    assert knowledge.is_battery_backed("x")
    assert knowledge.is_battery_backed("w")
    assert not knowledge.is_battery_backed("y")
    assert not knowledge.is_battery_backed("unassigned")


def test_no_houses_knows_nothing() -> None:
    """Without Houses nothing is Battery-backed and nothing is producing."""
    knowledge = HouseKnowledge([], {"d2": dtu(inverter("p"))})
    assert not knowledge.is_battery_backed("p")
    assert not knowledge.other_dtu_producing("d1", ["q"])


def test_producing_pv_inverter_of_same_house_on_other_dtu() -> None:
    """The plain positive case."""
    knowledge = HouseKnowledge(
        [house("a", ["q", "p"])], {"d1": dtu(inverter("q")), "d2": dtu(inverter("p"))}
    )
    assert knowledge.other_dtu_producing("d1", ["q"])


def test_power_threshold_is_inclusive() -> None:
    """At the minimum power counts, below does not."""
    for power, expected in [
        (MIN_PRODUCING_POWER, True),
        (MIN_PRODUCING_POWER - 0.1, False),
        (None, False),
    ]:
        knowledge = HouseKnowledge(
            [house("a", ["q", "p"])], {"d2": dtu(inverter("p", power=power))}
        )
        assert knowledge.other_dtu_producing("d1", ["q"]) is expected


def test_battery_backed_producer_does_not_count() -> None:
    """A Battery-backed Inverter producing at night proves nothing."""
    knowledge = HouseKnowledge(
        [house("a", ["q", "p"], ["p"])], {"d2": dtu(inverter("p", power=300))}
    )
    assert not knowledge.other_dtu_producing("d1", ["q"])


def test_battery_backed_in_another_house_does_not_count() -> None:
    """The mark of any House makes the Inverter Battery-backed."""
    knowledge = HouseKnowledge(
        [house("a", ["q", "p"]), house("b", ["p"], ["p"])],
        {"d2": dtu(inverter("p"))},
    )
    assert not knowledge.other_dtu_producing("d1", ["q"])


def test_not_producing_flag_does_not_count() -> None:
    """The DTU must report the Inverter as producing."""
    knowledge = HouseKnowledge(
        [house("a", ["q", "p"])], {"d2": dtu(inverter("p", producing=False))}
    )
    assert not knowledge.other_dtu_producing("d1", ["q"])


def test_stale_producer_does_not_count() -> None:
    """Data as old as that DTU's own staleness time is no longer fresh."""
    houses = [house("a", ["q", "p"])]
    fresh = HouseKnowledge(houses, {"d2": dtu(inverter("p", data_age=119))})
    stale = HouseKnowledge(houses, {"d2": dtu(inverter("p", data_age=120))})
    own_limit = HouseKnowledge(
        houses, {"d2": dtu(inverter("p", data_age=200), staleness=300)}
    )
    assert fresh.other_dtu_producing("d1", ["q"])
    assert not stale.other_dtu_producing("d1", ["q"])
    assert own_limit.other_dtu_producing("d1", ["q"])


def test_producer_on_down_dtu_does_not_count() -> None:
    """A DTU that does not answer contributes nothing."""
    knowledge = HouseKnowledge(
        [house("a", ["q", "p"])], {"d2": dtu(inverter("p"), ok=False)}
    )
    assert not knowledge.other_dtu_producing("d1", ["q"])


def test_dtu_without_data_does_not_count() -> None:
    """A DTU that never delivered a snapshot contributes nothing."""
    empty = SimpleNamespace(data=None, last_update_success=False, staleness_time=120)
    knowledge = HouseKnowledge([house("a", ["q", "p"])], {"d2": empty})
    assert not knowledge.other_dtu_producing("d1", ["q"])


def test_producer_of_other_house_does_not_count() -> None:
    """Only Inverters of a House that also has an Inverter here count."""
    knowledge = HouseKnowledge(
        [house("a", ["q"]), house("b", ["p"])], {"d2": dtu(inverter("p"))}
    )
    assert not knowledge.other_dtu_producing("d1", ["q"])


def test_producer_on_same_dtu_does_not_count() -> None:
    """An Inverter on this very DTU says nothing about other DTUs."""
    knowledge = HouseKnowledge(
        [house("a", ["q", "p"])], {"d1": dtu(inverter("q"), inverter("p"))}
    )
    assert not knowledge.other_dtu_producing("d1", ["q", "p"])


def test_dtu_serving_two_houses_uses_both() -> None:
    """One House with a producer elsewhere is enough."""
    houses = [house("a", ["q", "p1"]), house("b", ["r", "p2"])]
    only_b = HouseKnowledge(
        houses,
        {
            "d1": dtu(inverter("q"), inverter("r")),
            "d2": dtu(inverter("p1", power=10), inverter("p2", power=200)),
        },
    )
    none = HouseKnowledge(
        houses,
        {
            "d1": dtu(inverter("q"), inverter("r")),
            "d2": dtu(inverter("p1", power=10), inverter("p2", power=10)),
        },
    )
    assert only_b.other_dtu_producing("d1", ["q", "r"])
    assert not none.other_dtu_producing("d1", ["q", "r"])


def test_dtus_added_later_are_seen() -> None:
    """Houses are known before the DTUs; the mapping may fill up afterwards."""
    dtus: dict[str, Any] = {}
    knowledge = HouseKnowledge([house("a", ["q", "p"])], dtus)
    assert not knowledge.other_dtu_producing("d1", ["q"])
    dtus["d2"] = dtu(inverter("p"))
    assert knowledge.other_dtu_producing("d1", ["q"])
