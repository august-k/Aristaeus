"""Refactor of the CannonPlacement class."""
from typing import Dict, List, Optional, Set, Tuple, Union, TYPE_CHECKING
import numpy as np
from scipy.signal import convolve2d

from bot.consts import DESIRABILITY_KERNEL, INVALID_BLOCK
from bot.tools.grids import modify_two_by_two
from sc2.position import Point2

from MapAnalyzer.MapData import MapData

if TYPE_CHECKING:
    from ares import AresBot, ManagerMediator


class WallingLocations:
    """Find places to put wall components."""

    def __init__(self, ai: AresBot, map_data: MapData, mediator: ManagerMediator):
        """Set up the walling locations class.

        Parameters
        ----------
        ai
        map_data
        mediator
        """
        self.ai: AresBot = ai
        self.map_data: MapData = map_data
        self.mediator: ManagerMediator = mediator

    def generate_basic_walling_grid(
        self, add_to_blocked_positions: Optional[List[Point2]] = None
    ) -> np.ndarray:
        """Create the pathing grid used for walling.

        Parameters
        ----------
        add_to_blocked_positions : Optional[List[Point2]]
            Placement positions of 2x2 buildings we don't want to use in pathing.

        Returns
        -------
        np.ndarray :
            The pathing grid.

        """
        # get the standard pathing grid
        basic_grid = self.map_data.get_walling_grid()

        # create a new grid of the same size
        basic_walling_grid = np.zeros(basic_grid.shape, dtype=np.float32)

        # swap pathable and unpathable points
        basic_walling_grid[np.where(basic_grid != np.inf)] = np.inf
        basic_walling_grid[np.where(basic_grid == np.inf)] = 1

        # block off the designated positions
        if add_to_blocked_positions:
            for pos in add_to_blocked_positions:
                modify_two_by_two(basic_walling_grid, pos, np.inf)

        return basic_walling_grid


class BuildingPlacement:
    """Find places to put buildings."""

    def __init__(self, ai: AresBot, map_data: MapData, mediator: ManagerMediator):
        """Set up the building placement class.

        Parameters
        ----------
        ai
        map_data
        mediator
        """
        self.ai: AresBot = ai
        self.map_data: MapData = map_data
        self.mediator: ManagerMediator = mediator

    def perform_convolutions(
        self,
        x_bound: Tuple[int, int],
        y_bound: Tuple[int, int],
        terrain_height: Set[int],
        pathing_grid: np.ndarray,
        avoid_positions: Optional[List[Union[Point2, Tuple[int, int]]]] = None,
    ) -> Tuple[Dict, Dict]:
        """Convolve grids and return the dictionaries.

        Parameters
        ----------
        x_bound : Tuple[int, int]
            Minimum and maximum values of x to include in the possible locations.
        y_bound : Tuple[int, int]
            Minimum and maximum values of y to include in the possible locations.
        terrain_height : Set[int]
            All terrain heights that are part of the target region.
        pathing_grid : np.ndarray
            MapAnalyzer-style ground pathing grid to use.
        avoid_positions: Optional[List[Union[Point2, Tuple[int, int]]]]
            Positions where buildings are planned and need their tiles considered
            invalid.

        Returns
        -------
        Tuple[Dict[Tuple[int, int], int], Dict[Tuple[int, int], int] :
            Valid and invalid positions as a dictionary of building placement to
            convolution result.

        """
        # get the grid and our boundaries
        grid = self.generate_convolution_grid(
            x_bound, y_bound, terrain_height, pathing_grid, avoid_positions
        )
        x_min, _x_max = x_bound
        y_min, _y_max = y_bound

        # perform convolution and identify valid blocks
        placements = convolve2d(grid, DESIRABILITY_KERNEL, mode="valid")

        # set up location dictionaries
        valid_blocks: Dict[Tuple[int, int], int] = {}
        valid_non_blocking_positions: Dict[Tuple[int, int], int] = {}

        # go through the convolution result and filter into blocks and invalid blocks
        for x in range(placements.shape[0]):
            for y in range(placements.shape[1]):
                point = (x + x_min + 2, y + y_min + 2)
                score = placements[x][y]
                if score >= 4096:
                    # invalid placement
                    continue
                elif score in INVALID_BLOCK:
                    # valid placement, but it doesn't block
                    valid_non_blocking_positions[point] = score
                else:
                    valid_blocks[point] = score
        return valid_blocks, valid_non_blocking_positions

    def generate_convolution_grid(
        self,
        x_bound: Tuple[int, int],
        y_bound: Tuple[int, int],
        terrain_height: Set[int],
        pathing_grid: np.ndarray,
        avoid_positions: Optional[List[Union[Point2, Tuple[int, int]]]] = None,
    ) -> np.ndarray:
        """Generate the grids to convolve based on pathing and placement.

        Parameters
        ----------
        x_bound : Tuple[int, int]
            Minimum and maximum values of x to include in the possible locations.
        y_bound : Tuple[int, int]
            Minimum and maximum values of y to include in the possible locations.
        terrain_height : Set[int]
            All terrain heights that are part of the target region.
        pathing_grid : np.ndarray
            MapAnalyzer-style ground pathing grid to use.
        avoid_positions: Optional[List[Union[Point2, Tuple[int, int]]]]
            Positions where buildings are planned and need their tiles considered
            invalid.

        Returns
        -------
        np.ndarray :
            Grid of legal tiles to use, ready for convolution.

        """
        # create a grid of all valid tiles of the shape determined by x and y boundaries
        x_min, x_max = x_bound
        y_min, y_max = y_bound
        convolution_grid = np.ones((x_max - x_min + 1, y_max - y_min + 1))

        # if the tile can't actually be used for placements, set its value to 0
        for i in range(*x_bound):
            for j in range(*y_bound):
                if (
                    pathing_grid[i][j] == 1
                    and self.ai.game_info.placement_grid.data_numpy[j][i] == 1
                    and self.ai.game_info.terrain_height.data_numpy[j][i]
                    in terrain_height
                ):
                    convolution_grid[i - x_min][j - y_min] = 0

        # avoid using tiles where buildings are going
        # TODO: see if this broke something in the refactor,
        #   the comment and the code disagreed
        if avoid_positions:
            for pos in avoid_positions:
                if (x_min < pos[0] < x_max) and (y_min < pos[1] < y_max):
                    modify_two_by_two(convolution_grid, pos, 0)

        return convolution_grid
