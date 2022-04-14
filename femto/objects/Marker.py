from femto.objects.Waveguide import Waveguide
# from femto.compiler.PGMCompiler import PGMCompiler
# import numpy as np
# import pandas as pd
# import matplotlib.pyplot as plt
import warnings
from typing import List


class Marker(Waveguide):
    def __init__(self,
                 depth: float = 0.001,
                 speed: float = 1,
                 num_scan: int = 1):
        super(Marker, self).__init__(num_scan)

        self.depth = depth
        self.speed = speed
        self._M = {}

    def cross(self,
              position: List[float],
              lx: float,
              ly: float,
              speed_pos: float = 5):
        """
        Cross marker

        The function computes the point of a cross marker of given widht along
        x- and y-direction.

        Parameters
        ----------
        position : List[float]
        2D ordered coordinate list that specifies the cross position [mm].
            position[0] -> X
            position[1] -> Y
        lx : float
            Length of the cross marker along x [mm].
        ly : float
            Length of the cross marker along y [mm].
        speed_pos : float, optional
            Shutter closed transition speed [mm/s]. The default is 5.

        Returns
        -------
        None.

        """
        if len(position) == 2:
            position.append(self.depth)
        elif len(position) == 3:
            position[2] = self.depth
            warnings.warn('Given 3D coordinate list. ' +
                          f'Z-coordinate is overwritten to {self.depth} mm.')
        else:
            raise ValueError('Given invalid position.')

        self.start(position)
        self.linear([-lx/2, 0, 0], speed=speed_pos, shutter=0)
        self.linear([lx, 0, 0], speed=self.speed)
        self.linear([-lx/2, 0, 0], speed=speed_pos, shutter=0)
        self.linear([0, -ly/2, 0], speed=speed_pos, shutter=0)
        self.linear([0, ly, 0], speed=self.speed)
        self.linear([0, -ly/2, 0], speed=speed_pos, shutter=0)
        self.end(speed_pos)

    def ruler(self, y_ticks, speed=1, speed_pos=5):
        pass


if __name__ == '__main__':
    c = Marker()
    c.cross([5, 5, 0], 1, 0.60)
