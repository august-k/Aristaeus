from typing import TYPE_CHECKING

from sc2.ids.ability_id import AbilityId
from sc2.ids.buff_id import BuffId
from sc2.position import Point2
from sc2.units import Units

from ares.behaviors.macro import SpawnController
from ares.consts import UnitRole
from ares.cython_extensions.geometry import cy_towards
from ares.cython_extensions.units_utils import cy_closest_to
from ares.managers.manager_mediator import ManagerMediator
from ares.managers.manager import Manager

from sc2.ids.unit_typeid import UnitTypeId as UnitID

if TYPE_CHECKING:
    from ares import AresBot

# we always want one of each
CORE_STRUCTURES: list[UnitID] = [
    UnitID.GATEWAY,
    UnitID.CYBERNETICSCORE,
    UnitID.STARGATE,
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

        self._built_single_void: bool = False
        self._built_extra_production_pylon: bool = False

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

        if not self._built_single_void:
            if (
                self.ai.can_afford(UnitID.VOIDRAY)
                and UnitID.FLEETBEACON in structures_dict
                and self.ai.structures.filter(
                    lambda u: u.type_id == UnitID.STARGATE and u.is_ready and u.is_idle
                )
            ):
                self.ai.train(UnitID.VOIDRAY)
                self._built_single_void = True

    async def _build_structure(
        self,
        structure_type: UnitID,
        pos: Point2,
        max_distance: int = 20,
        random_alternative: bool = True,
    ) -> None:
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
            except IndexError:
                pass

    async def _build_pylons(self, building_counter: dict[UnitID, int]) -> None:
        if (
            self.ai.supply_left < 4
            and self.ai.already_pending(UnitID.PYLON) == 0
            and building_counter[UnitID.PYLON] == 0
        ) or not self._built_extra_production_pylon:
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
        if (
            self.ai.gas_buildings.amount < 2
            and self.ai.can_afford(UnitID.ASSIMILATOR)
            and building_counter[UnitID.ASSIMILATOR] == 0
            and UnitID.GATEWAY in structures_dict
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

        # add a second stargate
        if (
            self.ai.vespene > 260
            and UnitID.FLEETBEACON in structures_dict
            and UnitID.STARGATE in structures_dict
            and len(structures_dict[UnitID.STARGATE]) == 1
            and building_counter[UnitID.STARGATE] == 0
        ):
            try:
                if pylons := structures_dict[UnitID.PYLON].filter(lambda p: p.is_ready):
                    await self._build_structure(
                        UnitID.STARGATE,
                        cy_closest_to(self.ai.start_location, pylons).position,
                    )
            except IndexError:
                pass

    def _chrono_structures(self):
        for nexus in self.ai.townhalls:
            if AbilityId.EFFECT_CHRONOBOOSTENERGYCOST in nexus.abilities:
                nexuses = [
                    n
                    for n in self.ai.townhalls
                    if not n.is_idle and not n.has_buff(BuffId.CHRONOBOOSTENERGYCOST)
                ]
                if len(nexuses) > 0:
                    nexus(AbilityId.EFFECT_CHRONOBOOST, nexuses[0])
                    return

                stargates = [
                    s
                    for s in self.ai.structures
                    if not s.is_idle and not s.has_buff(BuffId.CHRONOBOOSTENERGYCOST)
                ]
                if len(stargates) > 0:
                    nexus(AbilityId.EFFECT_CHRONOBOOSTENERGYCOST, stargates[0])
                    return
