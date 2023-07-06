"""Handle Cannon Rush tasks."""

from typing import Dict, Set, TYPE_CHECKING, Any

from ares.consts import ManagerName, ManagerRequestType
from ares.managers.manager import Manager
from ares.managers.manager_mediator import IManagerMediator, ManagerMediator
from bot.tools.cannon_placement import CannonPlacement

from ares.managers.path_manager import MapData

if TYPE_CHECKING:
    from ares import AresBot


class CannonRushManager(Manager, IManagerMediator):
    """Handle cannon rush tasks."""

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
        self.map_data: MapData = self.manager_mediator.get_map_data_object
        self.cannon_placement: CannonPlacement = CannonPlacement(ai, self.map_data)
        self.cannon_rush_worker_tags: Set[int] = set()

    def manager_request(
        self,
        receiver: ManagerName,
        request: ManagerRequestType,
        reason: str = None,
        **kwargs
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
        pass

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
