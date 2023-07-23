from typing import TYPE_CHECKING

from ares.behaviors.macro import SpawnController
from ares.consts import UnitRole
from ares.cython_extensions.general_utils import cy_unit_pending
from ares.cython_extensions.geometry import cy_towards
from ares.cython_extensions.units_utils import cy_closest_to
from ares.managers.manager import Manager
from ares.managers.manager_mediator import ManagerMediator
from sc2.dicts.upgrade_researched_from import UPGRADE_RESEARCHED_FROM
from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.ids.upgrade_id import UpgradeId
from sc2.position import Point2
from sc2.units import Units

if TYPE_CHECKING:
    from ares import AresBot

# we always want one of each
CORE_STRUCTURES: list[UnitID] = [
    UnitID.GATEWAY,
    UnitID.CYBERNETICSCORE,
    UnitID.STARGATE,
]

DESIRED_UPGRADES: list[UpgradeId] = [
    UpgradeId.TEMPESTGROUNDATTACKUPGRADE,
    UpgradeId.PROTOSSAIRARMORSLEVEL1,
    UpgradeId.PROTOSSAIRARMORSLEVEL2,
]


class ProductionManager(Manager):
    def __init__(
        self,
        ai: "AresBot",
        config: dict,
        mediator: ManagerMediator,
    ) -> None:
        """Set up the manager.

        Parameters
        ----------
        ai :
            Bot object that will be running the game
        config :
            Dictionary with the data from the configuration file
        mediator :
            ManagerMediator used for getting information from other managers.

        Returns
        -------

        """
        super().__init__(ai, config, mediator)

        self._built_single_oracle: bool = False
        self._built_extra_production_pylon: bool = False
        # can use a single chrono for the oracle
        self._oracle_chrono: bool = False

    async def update(self, iteration: int) -> None:
        """Handle production.

        TODO: Add `AutoSupply` when that feature is ready in ares

        Parameters
        ----------
        iteration :
            The game iteration.
        """
        self.ai.register_behavior(
            SpawnController(
                army_composition_dict={
                    UnitID.TEMPEST: {"proportion": 1.0, "priority": 0},
                }
            )
        )
        building_counter: dict[UnitID, int] = self.manager_mediator.get_building_counter
        structures_dict: dict[
            UnitID, Units
        ] = self.manager_mediator.get_own_structures_dict

        await self._build_pylons(building_counter)
        self._build_probes(self.ai.ready_townhalls)
        await self._build_tempest_rush_structures(building_counter, structures_dict)
        self._chrono_structures()
        self._research_upgrades()

        # one off task to build an oracle
        if not self._built_single_oracle:
            if (
                self.ai.can_afford(UnitID.ORACLE)
                and UnitID.FLEETBEACON in structures_dict
                and self.ai.structures.filter(
                    lambda u: u.type_id == UnitID.STARGATE and u.is_ready and u.is_idle
                )
            ):
                self.ai.train(UnitID.ORACLE)
                self._built_single_oracle = True

    async def _build_structure(
        self,
        structure_type: UnitID,
        pos: Point2,
        max_distance: int = 20,
        random_alternative: bool = True,
    ) -> None:
        """Reusable method to build a structure.

        Automatically assigns worker and removes them from mining.

        Parameters
        ----------
        structure_type : UnitTypeId
            Structure we want to build
        pos : Point2
            Roughly where structure should go
        max_distance : int
            How far to search for placement
        random_alternative : bool
            If no placement is found, find any alternative.
        """
        if build_pos := await self.ai.find_placement(
            structure_type,
            pos,
            random_alternative=random_alternative,
            max_distance=max_distance,
        ):
            if worker := self.ai.mediator.select_worker(target_position=build_pos):
                self.ai.mediator.build_with_specific_worker(
                    worker=worker, structure_type=structure_type, pos=build_pos
                )
                self.ai.mediator.assign_role(tag=worker.tag, role=UnitRole.BUILDING)

    async def _build_core_structure(
        self,
        structure_id: UnitID,
        building_counter: dict[UnitID, int],
        structures_dict: dict[UnitID, Units],
    ) -> None:
        """Here to prevent repeated logic building core structures.

        Parameters
        ----------
        structure_id : UnitTypeId
            What we want to build
        building_counter : Dict[UnitTypeId, int]
            What is currently pending in the building tracker
        structures_dict : Dict[UnitTypeId, Units]
            Data structure of current buildings.
        """
        if (
            building_counter[structure_id] == 0
            and structure_id not in structures_dict
            and self.ai.tech_requirement_progress(structure_id) >= 1.0
        ):
            try:
                if pylons := structures_dict[UnitID.PYLON].filter(lambda p: p.is_ready):
                    await self._build_structure(
                        structure_id,
                        cy_closest_to(self.ai.start_location, pylons).position,
                    )
            except KeyError:
                pass

    async def _build_pylons(self, building_counter: dict[UnitID, int]) -> None:
        """Logic for when to add pylons.

        Parameters
        ----------
        building_counter : Dict[UnitTypeId, int]
            What is currently pending in the building tracker

        """
        if (
            self.ai.supply_left < 4
            and self.ai.already_pending(UnitID.PYLON) == 0
            and building_counter[UnitID.PYLON] == 0
        ) or (not self._built_extra_production_pylon and self.ai.vespene > 0):
            self._built_extra_production_pylon = True
            await self._build_structure(
                UnitID.PYLON,
                Point2(
                    cy_towards(
                        self.ai.start_location, self.ai.game_info.map_center, 7.0
                    )
                ),
            )

    def _build_probes(self, ready_townhalls: Units) -> None:
        """Add probes.

        Parameters
        ----------
        ready_townhalls : Units
            Current ready nexuses we can train from.
        """
        if (
            self.ai.supply_workers < 22
            and self.ai.can_afford(UnitID.PROBE)
            and self.ai.supply_left > 0
        ):
            if idle_ths := ready_townhalls.idle:
                for nexus in idle_ths:
                    nexus.train(UnitID.PROBE)

    async def _build_tempest_rush_structures(
        self, building_counter: dict[UnitID, int], structures_dict: dict[UnitID, Units]
    ) -> None:
        """Build everything we need towards Tempest tech.

        building_counter : Dict[UnitTypeId, int]
            What is currently pending in the building tracker
        structures_dict : Dict[UnitTypeId, Units]
            Data structure of current buildings.
        """
        max_gas_buildings = 2 if UnitID.GATEWAY in structures_dict else 1
        if (
            self.ai.gas_buildings.amount < max_gas_buildings
            and self.ai.can_afford(UnitID.ASSIMILATOR)
            and building_counter[UnitID.ASSIMILATOR] == 0
        ):
            if worker := self.ai.mediator.select_worker(
                target_position=self.ai.start_location
            ):
                geysers: Units = self.ai.vespene_geyser.filter(
                    lambda vg: not self.ai.gas_buildings.closer_than(2, vg)
                )
                self.ai.mediator.build_with_specific_worker(
                    worker=worker,
                    structure_type=UnitID.ASSIMILATOR,
                    pos=cy_closest_to(self.ai.start_location, geysers),
                )
                self.ai.mediator.assign_role(tag=worker.tag, role=UnitRole.BUILDING)

        for core_structure_id in CORE_STRUCTURES:
            await self._build_core_structure(
                core_structure_id, building_counter, structures_dict
            )

        # add fleetbeacon separate, since `tech_requirement_progress` doesn't work
        if (
            UnitID.FLEETBEACON not in structures_dict
            and UnitID.STARGATE in structures_dict
            and structures_dict[UnitID.STARGATE].ready
            and building_counter[UnitID.FLEETBEACON] == 0
        ):
            await self._build_core_structure(
                UnitID.FLEETBEACON, building_counter, structures_dict
            )

    def _chrono_structures(self):
        """Decide what to chrono."""
        for nexus in self.ai.townhalls:
            if AbilityId.EFFECT_CHRONOBOOSTENERGYCOST in nexus.abilities:
                stargates = [
                    s
                    for s in self.ai.structures
                    if not s.is_idle
                    and not s.has_buff(BuffId.CHRONOBOOSTENERGYCOST)
                    and s.type_id == UnitID.STARGATE
                ]
                if len(stargates) > 0:
                    if cy_unit_pending(self.ai, UnitID.TEMPEST):
                        nexus(AbilityId.EFFECT_CHRONOBOOSTENERGYCOST, stargates[0])
                        return
                    if not self._oracle_chrono:
                        nexus(AbilityId.EFFECT_CHRONOBOOSTENERGYCOST, stargates[0])
                        self._oracle_chrono = True

    def _research_upgrades(self):
        """Decide what to research."""
        # only get upgrades if stargate is already building a tempest
        if cy_unit_pending(self.ai, UnitID.TEMPEST) == 0:
            return

        structure_dict: dict[
            UnitID, Units
        ] = self.manager_mediator.get_own_structures_dict
        for upgrade_id in DESIRED_UPGRADES:
            researched_from: UnitID = UPGRADE_RESEARCHED_FROM[upgrade_id]
            cost = self.ai.calculate_cost(upgrade_id)
            # ensure there is always nearly enough for a tempest
            # before spending all the banked vespene
            if self.ai.vespene - cost.vespene < 160:
                continue
            if (
                researched_from in structure_dict
                and self.ai.can_afford(upgrade_id)
                and structure_dict[researched_from].idle
            ):
                self.ai.research(upgrade_id)
