from __future__ import annotations

import collections
import contextlib
import copy
import dataclasses
import itertools
import math
import pathlib
from types import TracebackType
from typing import Any
from typing import Callable
from typing import Deque
from typing import Generator
from typing import TypeVar

import dill
import matplotlib.pyplot as plt
import numpy as np
import numpy.typing as npt
from femto.helpers import flatten
from femto.helpers import listcast
from femto.helpers import pad
from scipy import interpolate

# Create a generic variable that can be 'PGMCompiler', or any subclass.
GC = TypeVar('GC', bound='PGMCompiler')


@dataclasses.dataclass(repr=False)
class PGMCompiler:

    filename: str
    n_glass: float = 1.50
    n_environment: float = 1.33
    export_dir: str = ''
    samplesize: tuple[float, float] = (100, 50)
    laser: str = 'PHAROS'
    home: bool = False
    new_origin: tuple[float, float] = (0.0, 0.0)
    warp_flag: bool = False
    rotation_angle: float = 0.0
    aerotech_angle: float = 0.0
    long_pause: float = 0.5
    short_pause: float = 0.05
    output_digits: int = 6
    speed_pos: float = 5.0
    flip_x: bool = False
    flip_y: bool = False

    _total_dwell_time: float = 0.0
    _shutter_on: bool = False
    _mode_abs: bool = True

    def __post_init__(self) -> None:
        if self.filename is None:
            raise ValueError("Filename is None, set 'filename' attribute")
        self.CWD: pathlib.Path = pathlib.Path.cwd()
        self._instructions: Deque[str] = collections.deque()
        self._loaded_files: list[str] = []
        self._dvars: list[str] = []

        self.fwarp: Callable[
            [npt.NDArray[np.float32], npt.NDArray[np.float32]], npt.NDArray[np.float32]
        ] = self.antiwarp_management(self.warp_flag)

        # Set rotation angle in radians for matrix rotations
        if self.rotation_angle:
            self.rotation_angle = math.radians(self.rotation_angle % 360)
        else:
            self.rotation_angle = float(0.0)

        # Set AeroTech angle between 0 and 359 for G84 command
        if self.aerotech_angle:
            self.aerotech_angle = self.aerotech_angle % 360
        else:
            self.aerotech_angle = float(0.0)

    @classmethod
    def from_dict(cls: type[GC], param: dict[str, Any]) -> GC:
        """Create an instance of the class from a dictionary.

        It takes a class and a dictionary, and returns an instance of the class with the dictionary's keys as the
        instance's attributes.

        Parameters
        ----------
        param, dict()
            Dictionary mapping values to class attributes.

        Returns
        -------
        Instance of class
        """
        return cls(**param)

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}@{id(self) & 0xFFFFFF:x}'

    def __enter__(self) -> PGMCompiler:
        """Context manager entry.

        The context manager takes care to automatically add the proper header file (from the `self.laser` attribute,
        add the G84 activation instruction (if needed) and printing some warning info for rotations.

        It can be use like:

        >>>with femto.PGMCompiler(filename, ind_rif) as gc:
        >>>     <code block>

        Returns
        -------
        The object itself.
        """

        self.header()
        self.dwell(1.0)
        self.instruction('\n')

        if self.rotation_angle:
            print(' BEWARE, ANGLE MUST BE IN DEGREE! '.center(38, '*'))
            print(f' Rotation angle is {self.rotation_angle:.3f} deg. '.center(38, '*'))
            print()

        if self.aerotech_angle:
            print(' BEWARE, G84 COMMAND WILL BE USED!!! '.center(39, '*'))
            print(' ANGLE MUST BE IN DEGREE! '.center(39, '*'))
            print(f' Rotation angle is {self.aerotech_angle:.3f} deg. '.center(39, '*'))
            print()
            self._enter_axis_rotation(angle=self.aerotech_angle)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Context manager exit.

        Returns
        -------
        None
        """

        if self.aerotech_angle:
            self._exit_axis_rotation()
            self._instructions.append('\n')
        if self.home:
            self.go_init()
        self.close()

    @property
    def xsample(self) -> float:
        """`x`-dimension of the sample

        Returns
        -------
        The absolute value of the `x` element of the samplesize array.
        """
        return float(abs(self.samplesize[0]))

    @property
    def ysample(self) -> float:
        """`y`-dimension of the sample

        Returns
        -------
        The absolute value of the `y` element of the samplesize array.
        """
        return float(abs(self.samplesize[1]))

    @property
    def neff(self) -> float:
        """Effective refractive index.

        Returns
        -------
        Effective refractive index of the waveguide.
        """
        return self.n_glass / self.n_environment

    @property
    def pso_label(self) -> str:
        """PSO command lable.

        If the laser is ANT, return Z, otherwise return X.

        Returns
        -------
        Lable for the PSO commands.
        """
        if self.laser.lower() not in ['ant', 'carbide', 'pharos', 'uwe']:
            raise ValueError(f'Laser can be only ANT, CARBIDE, PHAROS or UWE. Given {self.laser.upper()}.')
        if self.laser.lower() == 'ant':
            return 'Z'
        else:
            return 'X'

    @property
    def tshutter(self) -> float:
        """Shuttering delay.

        Function that gives the shuttering delay time given the fabrication laboratory.

        Returns
        -------
        Delay time [s].
        """
        if self.laser.lower() not in ['ant', 'carbide', 'pharos', 'uwe']:
            raise ValueError(f'Laser can be only ANT, CARBIDE, PHAROS or UWE. Given {self.laser.upper()}.')
        if self.laser.lower() == 'uwe':
            # mechanical shutter
            return 0.005
        else:
            # pockels cell
            return 0.000

    @property
    def dwell_time(self) -> float:
        """Total DWELL time.

        Returns
        -------
        Total pausing times in the G-code script.
        """
        return self._total_dwell_time

    def header(self) -> None:
        """Add header instructions.

        It reads the header file for the laser cutter and adds it to the instructions list.
        The user can specify the fabrication line to work in ``ANT``, ``CARBIDE``, ``PHAROS`` or ``UWE`` laser when
        the G-Code Compiler obj is instantiated.

        Returns
        -------
        None
        """

        if self.laser is None or self.laser.lower() not in ['ant', 'carbide', 'pharos', 'uwe']:
            raise ValueError(f'Fabrication line should be PHAROS, CARBIDE or UWE. Given {self.laser}.')

        header_name = f'header_{self.laser.lower()}.txt'
        with open(pathlib.Path(__file__).parent / 'utils' / header_name) as f:
            self._instructions.extend(f.readlines())
        self.instruction('\n')

    def dvar(self, variables: list[str]) -> None:
        """Add declared variable instructions.

        Adds the declaration of variables in a G-Code file.

        Parameters
        ----------
        variables : list(str)
            List of G-Code variables.

        Returns
        -------
        None
        """
        variables = listcast(flatten(variables))
        args = ' '.join(['${}'] * len(variables)).format(*variables)
        self._instructions.appendleft(f'DVAR {args}\n\n')

        # keep track of all variables
        self._dvars.extend([var.lower() for var in variables])

    def mode(self, mode: str = 'abs') -> None:
        """Movements mode.

        The function appends the mode string to the list of instructions. If the string is not 'abs' or 'inc',
        it will raise a ValueError.

        Parameters
        ----------
        mode: str, optional
            Operation mode of the movements commands. It can be ABSOLUTE or INCREMENTAL. The default value is ABSOLUTE.

        Returns
        -------
        None
        """
        if mode is None or mode.lower() not in ['abs', 'inc']:
            raise ValueError(f'Mode should be either ABSOLUTE (ABS) or INCREMENTAL (INC). {mode} was given.')

        if mode.lower() == 'abs':
            self._instructions.append('ABSOLUTE\n')
            self._mode_abs = True
        else:
            self._instructions.append('INCREMENTAL\n')
            self._mode_abs = False

    def comment(self, comstring: str) -> None:
        """Add a comment.

        Adds a comment to a G-Code file.

        Parameters
        ----------
        comstring : str
            Comment string.

        Returns
        -------
        None
        """

        if comstring:
            self._instructions.append(f'\n; {comstring}\n')
        else:
            self._instructions.append('\n')

    def shutter(self, state: str) -> None:
        """Open and close shutter.

        Adds the instruction to open (or close) the shutter to a G-Code file.
        The user specifies the state and the function compare it to the current state of the shutter (which is
        tracked internally during the compilation of the .pgm file).

        Parameters
        ----------
        state: str
            State of the shutter (`ON` or `OFF`).

        Returns
        -------
        None
        """

        if state is None or state.lower() not in ['on', 'off']:
            raise ValueError(f'Shutter state should be ON or OFF. Given {state}.')

        if state.lower() == 'on' and self._shutter_on is False:
            self._shutter_on = True
            self._instructions.append(f'PSOCONTROL {self.pso_label} ON\n')
        elif state.lower() == 'off' and self._shutter_on is True:
            self._shutter_on = False
            self._instructions.append(f'PSOCONTROL {self.pso_label} OFF\n')
        else:
            pass

    def dwell(self, pause: float) -> None:
        """Add pause.

        Parameters
        ----------
        pause : float
            Pause duration [s].

        Returns
        -------
        None
        """

        if pause is None or pause == float(0.0):
            return None
        self._instructions.append(f'DWELL {np.fabs(pause)}\n')
        self._total_dwell_time += np.fabs(pause)

    def set_home(self, home_pos: list[float]) -> None:
        """Set coordinates of present position.

        The user can set the current Aerotech postition to a particular set of coordinates, given as an input list.
        A variable can be excluded if set to ``None``. The function can be used to set a user-defined home position.

        Parameters
        ----------
        home_pos: list(float)
            List of coordinates `(x, y, z)` of the new value for the current point [mm].

        Returns
        -------
        None
        """

        if np.size(home_pos) != 3:
            raise ValueError(f'Given final position is not valid. 3 values required, given {np.size(home_pos)}.')

        if all(coord is None for coord in home_pos):
            raise ValueError('Given home position is (None, None, None). Give a valid home position.')

        args = self._format_args(*home_pos)
        self._instructions.append(f'G92 {args}\n')

    def move_to(self, position: list[float | None], speed_pos: float | None = None) -> None:
        """Move to target.

        Utility function to move to a given position with the shutter ``OFF``.
        The user can specify the target position and the positioning speed.

        Parameters
        ----------
        position: list(float, optional)
            List of target coordinates `(x, y, z)` [mm].
        speed_pos: float, optional
            Translation speed. The default value is ``self.speed_pos``.

        Returns
        -------
        None
        """
        if len(position) != 3:
            raise ValueError(f'Given final position is not valid. 3 values required, given {len(position)}.')

        if speed_pos is None and self.speed_pos is None:
            raise ValueError('The positioning speed is None. Set the "speed_pos" attribute or give a valid value.')
        speed_pos = self.speed_pos if speed_pos is None else speed_pos

        # close the shutter before the movements
        if self._shutter_on is True:
            self.shutter('OFF')

        xp, yp, zp = position
        args = self._format_args(xp, yp, zp, speed_pos)
        if all(coord is None for coord in position):
            self._instructions.append(f'{args}\n')
        else:
            self._instructions.append(f'G1 {args}\n')
        self.dwell(self.long_pause)
        self.instruction('\n')

    def go_origin(self) -> None:
        """Return to origin.

        Utility function, returns to the origin `(0,0,0)` with shutter ``OFF``.

        Returns
        -------
        None
        """
        self.comment('HOMING')
        self.move_to([0.0, 0.0, 0.0])

    def go_init(self) -> None:
        """Return to initial point.

        Utility function to return to the initial point of fabrication `(-2,0,0)` with shutter ``OFF``.

        Returns
        -------
        None
        """
        self.move_to([-2, 0, 0])

    @contextlib.contextmanager
    def axis_rotation(self, angle: float | None = None) -> Generator[PGMCompiler, None, None]:
        """Aerotech axis rotation (G84).

        Context manager for the G84 command. The user can specify the angle (in degree) of the axis rotation.

        Parameters
        ----------
        angle : float
            Value [deg] of the rotation angle

        Yields
        ------
        Current PGMCompiler instance
        """
        self._enter_axis_rotation(angle=angle)
        try:
            yield self
        finally:
            self._exit_axis_rotation()

    @contextlib.contextmanager
    def for_loop(self, var: str, num: int) -> Generator[PGMCompiler, None, None]:
        """Foor loop instruction.

        Context manager that manages a ``FOR`` loops in a G-Code file.

        Parameters
        ----------
        var : str
            Iterating variable.
        num : int
            Number of iterations.

        Yields
        ------
        Current PGMCompiler instance
        """
        if num is None:
            raise ValueError("Number of iterations is None. Give a valid 'scan' attribute value.")
        if num <= 0:
            raise ValueError("Number of iterations is 0. Set 'scan'>= 1.")

        if var is None:
            raise ValueError('Given variable is None. Give a valid varible.')
        if var.lower() not in self._dvars:
            raise ValueError(f'Given variable has not beed declared. Use dvar() method to declare ${var} variable.')

        self._instructions.append(f'FOR ${var} = 0 TO {int(num) - 1}\n')
        _temp_dt = self._total_dwell_time
        try:
            yield self
        finally:
            self._instructions.append(f'NEXT ${var}\n\n')

            # pauses should be multiplied by number of cycles as well
            self._total_dwell_time += int(num - 1) * (self._total_dwell_time - _temp_dt)

    @contextlib.contextmanager
    def repeat(self, num: int) -> Generator[PGMCompiler, None, None]:
        """Repeat loop instruction.

        Context manager that manages a ``REPEAT`` loops in a G-Code file.

        Parameters
        ----------
        num : int
            Number of iterations.

        Yields
        ------
        Current PGMCompiler instance
        """
        if num is None:
            raise ValueError("Number of iterations is None. Give a valid 'scan' attribute value.")
        if num <= 0:
            raise ValueError("Number of iterations is 0. Set 'scan'>= 1.")

        self._instructions.append(f'REPEAT {int(num)}\n')
        _temp_dt = self._total_dwell_time
        try:
            yield self
        finally:
            self._instructions.append('ENDREPEAT\n\n')

            # pauses should be multiplied by number of cycles as well
            self._total_dwell_time += int(num - 1) * (self._total_dwell_time - _temp_dt)

    def tic(self) -> None:
        """Start time measure.

        Print the current time (hh:mm:ss) in message panel. The function is intended to be used *before* the execution
        of an operation or script to measure its time performances.

        Returns
        -------
        None
        """
        self._instructions.append('MSGDISPLAY 1, "START #TS"\n\n')

    def toc(self) -> None:
        """Stop time measure.

        Print the current time (hh:mm:ss) in message panel. The function is intended to be used *after* the execution
        of an operation or script to measure its time performances.

        Returns
        -------
        None
        """
        self._instructions.append('MSGDISPLAY 1, "END   #TS"\n')
        self._instructions.append('MSGDISPLAY 1, "---------------------"\n')
        self._instructions.append('MSGDISPLAY 1, " "\n\n')

    def instruction(self, instr: str) -> None:
        """Add G-Code instruction.

        Adds a G-Code instruction passed as parameter to the PGM file.

        Parameters
        ----------
        instr : str
            G-Code instruction to add.

        Returns
        -------
        None
        """
        if instr.endswith('\n'):
            self._instructions.append(instr)
        else:
            self._instructions.append(instr + '\n')

    def load_program(self, filename: str, task_id: int = 2) -> None:
        """Load G-code script.

        Adds the instruction to `LOAD` an external G-Code script in the driver memory. The function is used for
        `FARCALL` programs.

        Parameters
        ----------
        filename : str
            Filename of the G-code script.
        task_id : int, optional
            Task ID number onto which the program will be loaded (and executed). The default value is 2.

        Returns
        -------
        None
        """
        if task_id is None:
            task_id = 2

        file = self._get_filepath(filename=filename, extension='pgm')
        self._instructions.append(f'PROGRAM {int(task_id)} LOAD "{file}"\n')
        self._loaded_files.append(file.stem)

    def remove_program(self, filename: str, task_id: int = 2) -> None:
        """Remove program from memory buffer.

        Adds the instruction to `REMOVE` a program from memory buffer in a G-Code file.

        Parameters
        ----------
        filename : str
            Filename of the G-code script.
        task_id : int, optional
            Task ID number onto which the program will be loaded (and executed). The default value is 2.

        Returns
        -------
        None
        """
        file = self._get_filepath(filename=filename, extension='pgm')
        if file.stem not in self._loaded_files:
            raise FileNotFoundError(
                f"The program {file} is not loaded. Load the file with 'load_program' before removing it."
            )
        self.programstop(task_id)
        self._instructions.append(f'REMOVEPROGRAM "{file.name}"\n')
        self._loaded_files.remove(file.stem)

    def programstop(self, task_id: int = 2) -> None:
        """Program stop.

        Add the instruction to stop the execution of an external G-Code script and empty the Task in which the
        program was running.

        Parameters
        ----------
        task_id : int, optional
            Task ID number onto which the program will be loaded (and executed). The default value is 2.

        Returns
        -------
        None
        """
        self._instructions.append(f'PROGRAM {int(task_id)} STOP\n')
        self._instructions.append(f'WAIT (TASKSTATUS({int(task_id)}, DATAITEM_TaskState) == TASKSTATE_Idle) -1\n')

    def farcall(self, filename: str) -> None:
        """FARCALL instruction.

        Adds the instruction to call and execute an external G-Code script in the current G-Code file.

        Parameters
        ----------
        filename : str
            Filename of the G-code script.

        Returns
        -------
        None
        """
        file = self._get_filepath(filename=filename, extension='.pgm')
        if file.stem not in self._loaded_files:
            raise FileNotFoundError(
                f"The program {file} is not loaded. Load the file with 'load_program' before the call."
            )
        self.dwell(self.short_pause)
        self._instructions.append(f'FARCALL "{file}"\n')

    def bufferedcall(self, filename: str, task_id: int = 2) -> None:
        """BUFFEREDCALL instruction.

        Adds the instruction to run an external G-Code script in queue mode.

        Parameters
        ----------
        filename : str
            Filename of the G-code script.
        task_id : int, optional
            Task ID number onto which the program will be loaded (and executed). The default value is 2.

        Returns
        -------
        None
        """
        file = self._get_filepath(filename=filename, extension='.pgm')
        if file.stem not in self._loaded_files:
            raise FileNotFoundError(
                f"The program {file} is not loaded. Load the file with 'load_program' before the call."
            )
        self.dwell(self.short_pause)
        self.instruction('\n')
        self._instructions.append(f'PROGRAM {task_id} BUFFEREDRUN "{file}"\n')

    def farcall_list(self, filenames: list[str], task_id: list[int] | int = 2) -> None:
        """Chiamatutto.

        Load and execute sequentially a list of G-Code scripts.

        Parameters
        ----------
        filenames : list(str)
            List of filenames of the G-code scripts to be executed.
        task_id : list(int), optional
            Task ID number onto which the program will be loaded (and executed). The default value is 2 for all the
            scripts in the filename list.

        Returns
        -------
        None
        """
        task_id = list(filter(None, listcast(task_id)))  # Remove None from task_id

        # Ensure task_id and filenames have the same length. If task_id is longer take a slice, pad with 0 otherwise.
        if len(task_id) > len(filenames):
            task_id = task_id[: len(filenames)]
        else:
            task_id = list(pad(task_id, len(filenames), 2))

        for fpath, t_id in zip(filenames, task_id):
            file = pathlib.Path(fpath)
            self.load_program(str(file), t_id)
            self.farcall(file.name)
            self.dwell(self.short_pause)
            self.remove_program(file.name, t_id)
            self.dwell(self.short_pause)
            self.instruction('\n\n')

    def write(self, points: npt.NDArray[np.float32]) -> None:
        """
        The function convert the quintuple (X,Y,Z,F,S) to G-Code instructions. The (X,Y,Z) coordinates are
        transformed using the transformation matrix that takes into account the rotation of a given rotation_angle
        and the homothety to compensate the (effective) refractive index different from 1. Moreover, if the warp_flag
        is True the points are compensated along the z-direction.

        The transformed points are then parsed together with the feed rate and shutter state coordinate to produce
        the LINEAR (G1) movements.

        :param points: Numpy matrix containing the values of the tuple [X,Y,Z,F,S] coordinates.
        :type points: numpy.ndarray
        :return: None
        """
        x, y, z, f_gc, s_gc = points

        # Transform points (rotations, z-compensation and flipping)
        x_gc, y_gc, z_gc = self.transform_points(x, y, z)

        # Convert points if G-Code commands
        args = [self._format_args(x, y, z, f) for (x, y, z, f) in zip(x_gc, y_gc, z_gc, f_gc)]
        for (arg, s) in itertools.zip_longest(args, s_gc):
            if s == 0 and self._shutter_on is True:
                self.instruction('\n')
                self.dwell(self.short_pause)
                self.shutter('OFF')
                self.dwell(self.long_pause)
                self.instruction('\n')
            elif s == 1 and self._shutter_on is False:
                self.instruction('\n')
                self.dwell(self.short_pause)
                self.shutter('ON')
                self.dwell(self.long_pause)
                self.instruction('\n')
            else:
                self._instructions.append(f'G1 {arg}\n')
        self.dwell(self.long_pause)
        self.instruction('\n')

    def close(self, filename: str | None = None, verbose: bool = False) -> None:
        """Close and export a G-Code file.

        The functions writes all the instructions in a .pgm file. The filename is specified during the class
        instatiation. If no extension is present, the proper one is automatically added.

        Parameters
        ----------
        filename: str, optional
            Name of the .pgm file. The default value is ``self.filename``.
        verbose: bool
            Flag to print info during .pgm file compilation.

        Returns
        -------
        None
        """

        # get filename and add the proper file extension
        pgm_filename = pathlib.Path(self.filename) if filename is None else pathlib.Path(filename)
        pgm_filename = pgm_filename.with_suffix('.pgm')

        # create export directory (mimicking the POSIX mkdir -p command)
        if self.export_dir:
            exp_dir = pathlib.Path(self.export_dir)
            if not exp_dir.is_dir():
                exp_dir.mkdir(parents=True, exist_ok=True)
            pgm_filename = exp_dir / pgm_filename

        # write instructions to file
        with open(pgm_filename, 'w') as f:
            f.write(''.join(self._instructions))
        self._instructions.clear()
        if verbose:
            print('G-code compilation completed.')

    # Geometrical transformations
    def transform_points(
        self,
        x: npt.NDArray[np.float32],
        y: npt.NDArray[np.float32],
        z: npt.NDArray[np.float32],
    ) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32], npt.NDArray[np.float32]]:
        """Transform points.

        The function takes in a set of points and apply a set of geometrical transformation (flip, translation,
        rotation and warp compensation).

        Parameters
        ----------
        x: numpy.ndarray
            Array of the `x`-coordinates.
        y: numpy.ndarray
            Array of the `y`-coordinates.
        z: numpy.ndarray
            Array of the `z`-coordinates.

        Returns
        -------
        tuple(numpy.ndarray, numpy.ndarray, numpy.ndarray)
            Transformed `x`, `y` and `z` arrays.
        """

        # normalize data
        x = np.asarray(x, dtype=np.float32)
        y = np.asarray(y, dtype=np.float32)
        z = np.asarray(z, dtype=np.float32)

        # translate points to new origin
        x -= self.new_origin[0]
        y -= self.new_origin[1]

        # flip x, y coordinates
        x, y = self.flip(x, y)

        # rotate points
        point_matrix = np.stack((x, y, z), axis=-1)
        x_t, y_t, z_t = np.matmul(point_matrix, self.t_matrix).T

        # compensate for warp
        if self.warp_flag:
            return self.compensate(x_t, y_t, z_t)
        return x_t, y_t, z_t

    def flip(
        self,
        xc: npt.NDArray[np.float32],
        yc: npt.NDArray[np.float32],
    ) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
        """Flip path.

        Flip the laser path along the `x` and `y` coordinates.

        Parameters
        ----------
        xc: numpy.ndarray
            Array of the `x`-coordinates.
        yc: numpy.ndarray
            Array of the `y`-coordinates.

        Returns
        -------
        tuple(numpy.ndarray, numpy.ndarray, numpy.ndarray)
            Flipped `x` and `y` arrays.
        """

        # disp = np.array([self.new_origin[0], self.new_origin[1], 0])
        fx = int(self.flip_x) * 2 - 1
        fy = int(self.flip_y) * 2 - 1
        mirror_matrix = np.array([[-fx, 0], [0, -fy]])
        flip_x, flip_y = mirror_matrix @ np.array([xc, yc])

        return flip_x, flip_y

    def compensate(
        self,
        x: npt.NDArray[np.float32],
        y: npt.NDArray[np.float32],
        z: npt.NDArray[np.float32],
    ) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32], npt.NDArray[np.float32]]:
        """Warp compensation.

        Returns the `z`-compensated points for the glass warp using ``self.fwarp`` function.

        Parameters
        ----------
        x: numpy.ndarray
            Array of the `x`-coordinates.
        y: numpy.ndarray
            Array of the `y`-coordinates.
        z: numpy.ndarray
            Array of the `z`-coordinates.

        Returns
        -------
        tuple(numpy.ndarray, numpy.ndarray, numpy.ndarray)
            Untouched `x`, `y` arrays and `z`-compensated array.
        """

        x_comp = copy.deepcopy(np.array(x))
        y_comp = copy.deepcopy(np.array(y))
        z_comp = copy.deepcopy(np.array(z))

        zwarp = np.array([float(self.fwarp(x, y)) for x, y in zip(x_comp, y_comp)])
        z_comp += zwarp / self.neff
        return x_comp, y_comp, z_comp

    @property
    def t_matrix(self) -> npt.NDArray[np.float32]:
        """Composition of `xy` rotation matrix and `z` refractive index compensation.

        Given the rotation rotation_angle and the refractive index, the function compute the transformation matrix as
        composition of rotation matrix (RM) and a homothety matrix (SM).

        Returns
        -------
        numpy.ndarray
            Composition of `xy` rotation matrix and `z` compensation for the refractive change between air (or water)
            and glass interface.
        """

        RM = np.array(
            [
                [np.cos(self.rotation_angle), -np.sin(self.rotation_angle), 0],
                [np.sin(self.rotation_angle), np.cos(self.rotation_angle), 0],
                [0, 0, 1],
            ]
        )
        SM = np.array(
            [
                [1, 0, 0],
                [0, 1, 0],
                [0, 0, 1 / self.neff],
            ]
        )
        TM = np.matmul(SM, RM).T
        return np.array(TM)

    def antiwarp_management(self, opt: bool, num: int = 16) -> interpolate.interp2d:
        """
        It fetches an antiwarp function in the current working direcoty. If it doesn't exist, it lets you create a new
        one. The number of sampling points can be specified.

        Parameters
        ----------
        opt: bool
            Flag to bypass the warp compensation.
        num: int
            Number of points for the interpolation of the sample's surface.

        Returns
        -------
        interpolate.interp2d
            Interpolating function S(x, y) of the surface of the sample.
        """

        if not opt:

            def fwarp(_x: float, _y: float) -> float:
                return 0.0

        else:
            if not all(self.samplesize):
                raise ValueError(f'Wrong sample size dimensions. Given ({self.samplesize[0]}, {self.samplesize[1]}).')
            function_pickle = self.CWD / 'fwarp.pkl'

            if function_pickle.is_file():
                with open(function_pickle, 'rb') as f_read:
                    fwarp = dill.load(f_read)
            else:
                fwarp = self.antiwarp_generation(self.samplesize, num)
                with open(function_pickle, 'wb') as f_write:
                    dill.dump(fwarp, f_write)
        return fwarp

    @staticmethod
    def antiwarp_generation(samplesize: tuple[float, float], num: int, margin: float = 2) -> interpolate.interp2d:
        """
        Helper for the generation of antiwarp function.
        The minimum number of data points required is (k+1)**2, with k=1 for linear, k=3 for cubic and k=5 for quintic
        interpolation.

        :param samplesize: glass substrate dimensions, (x-dim, y-dim)
        :type samplesize: Tuple(float, float)
        :param num: number of sampling points
        :type num: int
        :param margin: margin [mm] from the borders of the glass samples
        :type margin: float
        :return: warp function, `f(x, y)`
        :rtype: scipy.interpolate.interp2d
        """

        if num is None or num < 4**2:
            raise ValueError('I need more values to compute the interpolation.')

        num_side = int(np.ceil(np.sqrt(num)))
        xpos = np.linspace(margin, samplesize[0] - margin, num_side)
        ypos = np.linspace(margin, samplesize[1] - margin, num_side)
        xlist = []
        ylist = []
        zlist = []

        print('Insert focus height [in µm!] at:')
        for (x, y) in itertools.product(xpos, ypos):
            z_temp = input(f'X={x:.3f} Y={y:.3f}: \t')
            if z_temp == '':
                raise ValueError('You missed the last value.')
            else:
                xlist.append(x)
                ylist.append(y)
                zlist.append(float(z_temp) * 1e-3)

        # surface interpolation
        func_antiwarp = interpolate.interp2d(xlist, ylist, zlist, kind='cubic')

        # plot the surface
        xprobe = np.linspace(-3, samplesize[0] + 3)
        yprobe = np.linspace(-3, samplesize[1] + 3)
        zprobe = func_antiwarp(xprobe, yprobe)
        ax = plt.axes(projection='3d')
        ax.contour3D(xprobe, yprobe, zprobe, 200, cmap='viridis')
        ax.set_xlabel('X [mm]'), ax.set_ylabel('Y [mm]'), ax.set_zlabel('Z [mm]')
        # plt.show()
        return func_antiwarp

    # Private interface
    def _format_args(
        self, x: float | None = None, y: float | None = None, z: float | None = None, f: float | None = None
    ) -> str:
        """
        Utility function that creates a string prepending the coordinate name to the given value for all the given
        the coordinates ``[X,Y,Z]`` and feed rate ``F``.
        The decimal precision can be set by the user by setting the output_digits attribute.

        :param x: Value of the x-coordinate [mm]. The default is None.
        :type x: float
        :param y: Value of the y-coordinate [mm]. The default is None.
        :type y: float
        :param z: Value of the z-coordinate [mm]. The default is None.
        :type z: float
        :param f: Value of the f-coordinate [mm]. The default is None.
        :type f: float
        :return: Formatted string of the type: 'X<value> Y<value> Z<value> F<value>'.
        :rtype: str

        :raise ValueError: Try to move null speed.
        """
        args = []
        if x is not None:
            args.append(f'X{x:.{self.output_digits}f}')
        if y is not None:
            args.append(f'Y{y:.{self.output_digits}f}')
        if z is not None:
            args.append(f'Z{z:.{self.output_digits}f}')
        if f is not None:
            if f < 10 ** (-self.output_digits):
                raise ValueError('Try to move with F <= 0.0 mm/s. Check speed parameter.')
            args.append(f'F{f:.{self.output_digits}f}')
        joined_args = ' '.join(args)
        return joined_args

    @staticmethod
    def _get_filepath(filename: str, filepath: str | None = None, extension: str | None = None) -> pathlib.Path:
        """
        The function takes a filename and (optional) filepath, it merges the two and return a filepath.
        An extension parameter can be given as input. In that case the function also checks if the filename has
        the correct extension.

        :param filename: Name of the file that have to be loaded.
        :type filename: str
        :param filepath: Path of the folder containing the file. The default is None.
        :type filepath: str
        :param extension: File extension. The default is None.
        :type extension: str
        :return: Complete path of the file (filepath + filename).
        :rtype: pathlib.Path
        """

        if filename is None:
            raise ValueError('Given filename is None. Give a valid filename.')

        path = pathlib.Path(filename) if filepath is None else pathlib.Path(filepath) / filename
        if extension is None:
            return path

        ext = '.' + extension.split('.')[-1].lower()
        if path.suffix != ext:
            raise ValueError(f'Given filename has wrong extension. Given {filename}, required {ext}.')
        return path

    def _enter_axis_rotation(self, angle: float | None = None) -> None:
        self.comment('ACTIVATE AXIS ROTATION')
        self._instructions.append(f'G1 X{0.0:.6f} Y{0.0:.6f} Z{0.0:.6f} F{self.speed_pos:.6f}\n')
        self._instructions.append('G84 X Y\n')
        self.dwell(self.short_pause)

        if angle is None and self.aerotech_angle == 0.0:
            return

        angle = self.aerotech_angle if angle is None else float(angle % 360)
        self._instructions.append(f'G84 X Y F{angle}\n\n')
        self.dwell(self.short_pause)

    def _exit_axis_rotation(self) -> None:
        self.comment('DEACTIVATE AXIS ROTATION')
        self._instructions.append(f'G1 X{0.0:.6f} Y{0.0:.6f} Z{0.0:.6f} F{self.speed_pos:.6f}\n')
        self._instructions.append('G84 X Y\n')
        self.dwell(self.short_pause)


def main() -> None:
    from femto.waveguide import Waveguide
    from femto.helpers import dotdict

    # Parameters
    PARAM_WG = dotdict(scan=6, speed=20, radius=15, pitch=0.080, int_dist=0.007, lsafe=3, samplesize=(25, 3))
    PARAM_GC = dotdict(filename='testPGM.pgm', samplesize=PARAM_WG['samplesize'], rotation_angle=2.0, flip_x=True)

    # Build paths
    chip = [Waveguide(**PARAM_WG) for _ in range(2)]
    for i, wg in enumerate(chip):
        wg.start([-2, -wg.pitch / 2 + i * wg.pitch, 0.035])
        wg.linear([wg.lsafe, 0, 0])
        wg.sin_mzi((-1) ** i * wg.dy_bend, arm_length=1.0)
        wg.linear([wg.x_end, wg.lasty, wg.lastz], mode='ABS')
        wg.end()

    # Compilation
    with PGMCompiler(**PARAM_GC) as G:
        G.set_home([0, 0, 0])
        with G.repeat(PARAM_WG['scan']):
            for i, wg in enumerate(chip):
                G.comment(f'Modo: {i}')
                G.write(wg.points)
        G.move_to([None, 0, 0.1])
        G.set_home([0, 0, 0])


if __name__ == '__main__':
    main()
