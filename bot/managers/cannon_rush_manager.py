"""Handle Cannon Rush tasks."""

from typing import Dict, Set, TYPE_CHECKING, Any, Union, List, Optional

from sc2.ids.unit_typeid import UnitTypeId as UnitID
from sc2.unit import Unit
from sc2.units import Units

from ares.consts import ManagerName, ManagerRequestType, UnitRole, UnitTreeQueryType
from ares.cython_extensions.units_utils import cy_closest_to, cy_sorted_by_distance_to
from ares.managers.manager import Manager
from ares.managers.manager_mediator import IManagerMediator, ManagerMediator
from bot.tools.cannon_placement import CannonPlacement

from bot.consts import (
    BLOCKING,
    DESIRABILITY_KERNEL,
    FINAL_PLACEMENT,
    INVALID_BLOCK,
    LOCATION,
    POINTS,
    SCORE,
    TYPE_ID,
    WEIGHT,
)

from ares.managers.path_manager import MapData

if TYPE_CHECKING:
    from ares import AresBot


class CannonRushManager(Manager, IManagerMediator):
    """Handle cannon rush tasks."""

    map_data: MapData
    cannon_placement: CannonPlacement

    def __init__(
        self,
        ai: "AresBot",
        config: Dict,
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
        super(CannonRushManager, self).__init__(ai, config, mediator)

        self.manager_requests_dict = {
            "RegisterCannonRushWorker": lambda kwargs: self.register_cannon_rush_worker(
                **kwargs
            )
        }
        self.cannon_rush_worker_tags: Set[int] = set()
        self.high_ground_pylon_established: bool = False

    async def initialise(self) -> None:
        self.map_data: MapData = self.manager_mediator.get_map_data_object
        self.cannon_placement: CannonPlacement = CannonPlacement(self.ai, self.map_data)

    def manager_request(
        self,
        receiver: ManagerName,
        request: ManagerRequestType,
        reason: str = None,
        **kwargs,
    ) -> Any:
        """To be implemented by managers that inherit from IManagerMediator interface.

        Parameters
        ----------
        receiver :
            The Manager the request is being sent to.
        request :
            The Manager that made the request
        reason :
            Why the Manager has made the request
        kwargs :
            If the ManagerRequest is calling a function, that function's keyword
            arguments go here.

        Returns
        -------

        """
        return self.manager_requests_dict[request](kwargs)

    async def update(self, _iteration: int) -> None:
        """Update cannon rush status, tasks, and objectives.

        Parameters
        ----------
        _iteration :
            The current game iteration.

        Returns
        -------

        """
        if not self.ai.build_order_runner.build_completed:
            return
        self.cannon_placement.update()
        worker_units = [self.ai.unit_tag_dict[t] for t in self.cannon_rush_worker_tags]

        # steal any idle ones
        if not worker_units:
            if (
                available_workers := self.manager_mediator.get_units_from_role(
                    role=UnitRole.GATHERING
                )
                .filter(lambda u: not u.is_carrying_resource)
                .take(2)
            ):
                self.cannon_rush_worker_tags = available_workers.tags
                self.manager_mediator.batch_assign_role(
                    tags=self.cannon_rush_worker_tags,
                    role=UnitRole.CONTROL_GROUP_ONE,
                )
                # remove from mining, otherwise can't assign new workers to min field
                for worker in available_workers:
                    self.manager_mediator.remove_worker_from_mineral(
                        worker_tag=worker.tag
                    )
            return

        # make sure we're keeping these units
        self.manager_mediator.batch_assign_role(
            tags=self.cannon_rush_worker_tags,
            role=UnitRole.CONTROL_GROUP_ONE,
        )
        next_building = self.cannon_placement.next_building
        # nothing to place
        if not next_building or self.ai.minerals < 100:
            self._keep_workers_safe(worker_units)
            return

        # pylon placement procedures
        # if next_building[TYPE_ID] == UnitID.PYLON:
        #     worker = cy_closest_to(next_building[LOCATION], worker_units)
        #     self.manager_mediator.build_with_specific_worker(
        #         worker=worker,
        #         structure_type=UnitID.PYLON,
        #         pos=next_building[LOCATION],
        #     )
        #     self._keep_workers_safe([w for w in worker_units if w.tag != worker.tag])

        if next_building:
            used_tag = self.place_building(next_building, worker_units)
            self._keep_workers_safe([w for w in worker_units if w.tag != used_tag])

    @property
    def cannon_rush_complete(self) -> bool:
        # TODO: Rework this as needed
        return UnitID.PHOTONCANNON in self.manager_mediator.get_own_structures_dict

    def _keep_workers_safe(self, units: Union[Units, List[Unit]]):
        for unit in units:
            unit.move(self.cannon_placement.initial_cannon)

    def place_building(
        self, next_building: Dict, worker_units: Union[List[Unit], Units]
    ) -> int:
        """Place the next building.

        Parameters
        ----------
        next_building : Dict
            Dictionary containing information about the next building to place.
        worker_units : Units
            Available workers.

        Returns
        -------
        int :
            The tag of the worker that was used.

        """
        sorted_workers = cy_sorted_by_distance_to(
            units=worker_units,
            position=self.cannon_placement.initial_cannon
            if next_building[FINAL_PLACEMENT]
            else next_building[LOCATION],
        )
        used_worker = sorted_workers[0]

        if not next_building[FINAL_PLACEMENT] or used_worker.distance_to(
            self.cannon_placement.initial_cannon
        ) < used_worker.distance_to(next_building[LOCATION]):
            # we either don't need to worry about walling ourselves out OR we're on the
            # correct side of the wall
            self.manager_mediator.build_with_specific_worker(
                worker=used_worker,
                structure_type=next_building[TYPE_ID],
                pos=next_building[LOCATION],
            )
        else:
            # move so that we're on the correct side of the wall
            used_worker.move(self.cannon_placement.initial_cannon)
        return used_worker.tag

    def register_cannon_rush_worker(self, tag: int) -> None:
        """Register a worker as a cannon rush worker.

        Parameters
        ----------
        tag : int
            The tag of the worker to register.

        Returns
        -------

        """
        self.cannon_rush_worker_tags.add(tag)

    def remove_unit(self, unit_tag: int) -> None:
        """Remove a dead unit from tracking.

        Parameters
        ----------
        unit_tag :
            Tag to be removed

        Returns
        -------

        """
        if unit_tag in self.cannon_rush_worker_tags:
            self.cannon_rush_worker_tags.remove(unit_tag)

    def secure_initial_cannon(self, cannon_workers: Units) -> bool:
        """Place the first cannon that will be used as our anchor for the cannon rush.

        Parameters
        ----------
        cannon_workers : Units
            The Probes currently assigned as rushers.

        Returns
        -------
        bool :
            Whether this step should be considered completed.

        """
        initial_cannons = self.manager_mediator.get_units_in_range(
            start_points=[self.cannon_placement.initial_cannon],
            distances=2,
            query_tree=UnitTreeQueryType.AllOwn,
        )
        if initial_cannons:
            if initial_cannons[0].ready.amount != 0:
                # a cannon is ready! success! victory is assured!
                # TODO: remove the above
                return True
            else:
                self.defend_pending_cannon(cannon_workers)

        # no cannons have been placed, time to fix that
        next_building = self.cannon_placement.next_building
        # nothing to place
        if not next_building or self.ai.minerals < 100:
            self._keep_workers_safe(cannon_workers)
            return False

    def defend_pending_cannon(self, cannon_workers: Units) -> None:
        """A cannon has been started but it isn't finished; defend it.

        Parameters
        ----------
        cannon_workers : Units
            The Probes currently assigned as rushers.

        Returns
        -------

        """

    def cancel_pylon(self, pylon: Unit):
        """Prevent Pylons we don't need from finishing.

        Parameters
        ----------
        pylon :
            The Pylon to cancel

        Returns
        -------

        """
