from itertools import zip_longest
from math import radians
import numpy as np
import os
import pandas as pd
from pathlib import Path
from typing import List
from collections.abc import Iterable
from collections import deque
import glob

CWD = os.path.dirname(os.path.abspath(__file__))


class PGMCompiler:
    def __init__(self,
                 filename: str,
                 ind_rif: float,
                 angle: float = 0.0,
                 fabrication_line: str = 'CAPABLE',
                 long_pause: float = 0.25,
                 short_pause: float = 0.15,
                 output_digits: int = 6):

        self.filename = filename
        self.fabrication_line = fabrication_line
        self.long_pause = long_pause
        self.short_pause = short_pause

        self.ind_rif = ind_rif
        self.angle = radians(angle % 360)
        if angle != 0:
            print(' BEWARE ANGLES MUST BE IN DEGREE!! '.center(39, "*"))
            print(f' Given alpha = {angle % 360:.3f} deg. '.center(39, "*"))

        self.output_digits = output_digits

        self._num_repeat = 0
        self._num_for = 0
        self._total_dwell_time = 0.0
        self._shutter_on = False
        self._loaded_files = []

        self._instructions = deque()

    def __enter__(self):
        """
        Context manager entry

        Can use like:
        with femto.PGMCompiler(filename, ind_rif) as gc:
            <code block>
        """
        self.header()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Context manager exit
        """
        self.compile_pgm()

    # Methods
    def header(self):
        """
        HEADER.

        The function print the header file of the G-Code file. The user can
        specify the fabrication line to work in CAPABLE or FIRE LINE1 as
        parameter when the G-Code Compiler object is instantiated.

        Returns
        -------
        None.

        """
        assert self.fabrication_line.upper() in ['CAPABLE', 'FIRE'], \
            ('Specified fabrication line is neither CAPABLE nor FIRE. '
             f'Given {self.fabrication_line.upper()}.')

        if self.fabrication_line.upper() == 'CAPABLE':
            with open(os.path.join(CWD, 'header_capable.txt')) as fd:
                self._instructions.extend(fd.readlines())
        else:
            with open(os.path.join(CWD, 'header_fire.txt')) as fd:
                self._instructions.extend(fd.readlines())

    def dvar(self, variables: List[str]):
        """
        DECLARATION OF VARIABLE.

        Fuction to add the declaration of variables in a G-Code file.

        Parameters
        ----------
        variables : List[str]
            List of variables names.

        Returns
        -------
        None.

        """
        args = ' '.join(["${}"]*len(variables)).format(*variables)
        self._instructions.appendleft(f'DVAR {args}\n')

    def comment(self, comstring: str):
        """
        COMMENT.

        Add a comment to a G-Code file.

        Parameters
        ----------
        comstring : str
            Content of the comment (without line-break character).

        Returns
        -------
        None.

        """
        self._instructions.append(f'; {comstring}\n')

    def shutter(self, state: str):
        """
        SHUTTER.

        Add the instruction to open (close) the shutter to a G-Code file only
        when necessary.
        The user specifies the state and the function compare it to the
        current state of the shutter (which is tracked internally during
        the compilation of the .pgm file). The instruction is printed to file
        only if the new state differs from the current one.

        Parameters
        ----------
        state : str
            New state of the shutter. 'ON' or 'OFF'

        Returns
        -------
        None.

        """
        assert state.upper() in ['ON', 'OFF'], \
            ('Specified shutter state is neither ON nor OFF. '
             f'Given {state.upper()}.')

        if state.upper() == 'ON' and self._shutter_on is False:
            self._shutter_on = True
            self._instructions.append('PSOCONTROL X ON\n')
        elif state.upper() == 'OFF' and self._shutter_on is True:
            self._shutter_on = False
            self._instructions.append('PSOCONTROL X OFF\n')
        else:
            pass

    def dwell(self, pause: float):
        """
        DWELL.

        Add pause instruction to a G-Code file.

        Parameters
        ----------
        pause : float
            Value of the pause time [s].

        Returns
        -------
        None.

        """
        self._instructions.append(f'DWELL {pause}\n\n')
        self._total_dwell_time += float(pause)

    def set_home(self, home_pos: List[float]):
        """
        SET HOME.

        This function defines a preset position or a software home position to
        the one specified in the input list.
        To exclude a variable set it to None.

        Example:
            Set current (X,Y) position to (1,2), leave Z unchanged
            >> gc = PGMCompiler()
            >> gc.set_home([1,2,None])

        Parameters
        ----------
        home_pos : List[float]
            Ordered coordinate list that specifies software home position [mm].
            home_pos[0] -> X
            home_pos[1] -> Y
            home_pos[2] -> Z

        Returns
        -------
        None.

        """
        assert self._shutter_on is False, 'Try to move with shutter OPEN.'
        assert np.size(home_pos) == 3, \
            ('Given final position is not valid. ' +
             f'3 values are required, {np.size(home_pos)} were given.')

        x, y, z = home_pos
        args = self._format_args(x, y, z)
        self._instructions.append(f'G92 {args}\n')

    def homing(self):
        """
        HOMING.

        Utility function to return to the origin (0,0,0) with shutter OFF.

        Returns
        -------
        None.

        """
        self.comment('HOMING')
        self.move_to([0, 0, 0])

    def move_to(self, position: List[float], speed_pos: float = 50):
        """
        MOVE TO POSITION.

        Utility function to move to a given position with the shutter OFF.
        The user can specify the target position and the positioning speed.

        Parameters
        ----------
        position : List[float]
            Ordered coordinate list that specifies the target position [mm].
            position[0] -> X
            position[1] -> Y
            position[2] -> Z
        speed_pos : float, optional
            Positioning speed [mm/s]. The default is 50.

        Returns
        -------
        None.

        """
        assert np.size(position) == 3, \
            ('Given final position is not valid. ' +
             f'3 values are required, {np.size(position)} were given.')

        if self._shutter_on is True:
            self.shutter('OFF')

        x, y, z = position
        args = self._format_args(x, y, z, speed_pos)

        self._instructions.append(f'LINEAR {args}\n')
        self.dwell(self.long_pause)

    def for_loop(self, var: str, num: int):
        """
        FOR LOOP.

        Add the instruction th begin a FOR loop to a G-Code file.

        Parameters
        ----------
        var : str
            Name of the variable used for iteration.
        num : int
            Number of iterations.

        Returns
        -------
        None.

        """
        self._instructions.append(f'FOR ${var} = 0 TO {num-1}\n')
        self._num_for += 1

    def end_for(self, var: str):
        """
        END FOOR LOOP.

        Add the NEXT instruction to a G-Code file.

        Parameters
        ----------
        var : str
            Name of the variable used for the corresponding FOR loop.

        Returns
        -------
        None.

        """
        self._instructions.append(f'NEXT ${var}\n\n')
        self._num_for -= 1

    def rpt(self, num: int):
        """
        REPEAT.

        Add the REPEAT instruction to a G-Code file.

        Parameters
        ----------
        num : int
            Number of iterations.

        Returns
        -------
        None.

        """
        self._instructions.append(f'REPEAT {num}\n')
        self._num_repeat += 1

    def endrpt(self):
        """
        END REPEAT.

        Add the END REPEAT instruction to a G-Code file.

        Returns
        -------
        None.

        """
        self._instructions.append('ENDREPEAT\n\n')
        self._num_repeat -= 1

    def tic(self):
        """
        TIC.

        Print the current time (hh:mm:ss) in message panel. The function is
        intended to be used before the execution of an operation or script
        to measure its time performances.

        Returns
        -------
        None.

        """
        self._instructions.append('MSGDISPLAY 1, "INIZIO #TS"\n\n')

    def toc(self):
        """
        TOC.

        Print the current time (hh:mm:ss) in message panel. The function is
        intended to be used after the execution of an operation or script
        to measure its time performances.

        Returns
        -------
        None.

        """
        self._instructions.append('MSGDISPLAY 1, "FINE   #TS"\n')
        self._instructions.append('MSGDISPLAY 1, "---------------------"\n')
        self._instructions.append('MSGDISPLAY 1, " "\n\n')

    def load(self, filename: str, filepath: str = None):
        """
        LOAD PROGRAM.

        Add the instruction to LOAD a program in a G-Code file.

        Parameters
        ----------
        filename : str
            Name of the file that have to be loaded.
        filepath : str, optional
            Path of the folder containing the file. The default is None.

        Returns
        -------
        None.

        """
        file = self._parse_filepath(filename, extension='pgm')
        self._instructions.append(f'PROGRAM 0 LOAD "{file}"\n')
        self._loaded_files.append(file.stem)

    def remove(self, filename: str, filepath: str = None):
        """
        REMOVE PROGRAM.

        Add the instruction to REMOVE a program from memory buffer in a G-Code
        file.

        Parameters
        ----------
        filename : str
            Name of the file that have to be loaded.
        filepath : str, optional
            Path of the folder containing the file. The default is None.

        Returns
        -------
        None.

        """
        file = self._parse_filepath(filename, extension='pgm')
        self._instructions.append(f'REMOVEPROGRAM "{file}"\n')
        self._loaded_files.remove(file.stem)

    def farcall(self, filename: str):
        """
        FARCALL MODULE.


        Parameters
        ----------
        filename : str
            DESCRIPTION.
        filepath : str, optional
            DESCRIPTION. The default is None.

        Returns
        -------
        None.

        """
        file = self._parse_filepath(filename)
        assert file.stem in self._loaded_files, \
            (f'{file} not loaded. Cannot load it.')
        self._instructions.append(f'FARCALL "{file}"\n')
        self._instructions.append('PROGRAM 0 STOP\n')

    def point_to_instruction(self, M: pd.core.frame.DataFrame):
        """
        POINT TO INSTRUCTION.

        The function convert the quintuple (X,Y,Z,F,S) to G-Code instructions.
        The (X,Y,Z) coordinates are transformed using the transformation
        matrix that takes into account the rotation of a given angle and the
        homothety to compensate the (effective) refractive index different
        from 1.

        The transformed points are then parsed together with the feed rate and
        shutter state coordinate to produce the LINEAR movements.

        Parameters
        ----------
        M : pandas DataFrame
            DataFrame containing the values of the tuple [X,Y,Z,F,S]
            coordinates.

        Returns
        -------
        x : numpy.array
            Transformed values for the X coordinate.
        y : numpy.array
            Transformed values for the Y coordinate.
        z : numpy.array
            Transformed values for the Z coordinate.
        f : numpy.array
            Values for the F coordinate.
        s : numpy.array
            Values for the S coordinate.

        """
        c = np.column_stack((M['x'], M['y'], M['z']))
        c_rot = np.dot(self._compute_t_matrix(), c.T).T

        x = c_rot[:, 0]
        y = c_rot[:, 1]
        z = c_rot[:, 2]
        f = M['f'].to_numpy()
        s = M['s'].to_numpy()

        for i in range(len(x)):
            args = self._format_args(x[i], y[i], z[i], f[i])
            if s[i] == 0 and self._shutter_on is False:
                self._instructions.append(f'LINEAR {args}\n')
                self.dwell(self.long_pause)
            elif s[i] == 0 and self._shutter_on is True:
                self.shutter('OFF')
                self.dwell(self.short_pause)
            elif s[i] == 1 and self._shutter_on is False:
                self.shutter('ON')
                self._instructions.append(f'LINEAR {args}\n')
            else:
                self._instructions.append(f'LINEAR {args}\n')
        return (x, y, z, f, s)

    def make_trench(self,
                    col,
                    col_index,
                    base_folder,
                    dirname: str = 's-trench',
                    hbox: float = 0.075,
                    zoff: float = 0.020,
                    nboxz: int = 4,
                    deltaz: float = 0.0015,
                    angle: float = 0.0,
                    tspeed: float = 4,
                    ind_rif: float = 1.5/1.33,
                    speed_pos: float = 5,
                    pause: float = 0.5):

        trench_directory = os.path.join(dirname, f'trenchCol{col_index:03}')

        col_dir = os.path.join(os.getcwd(), trench_directory)
        os.makedirs(col_dir, exist_ok=True)

        # Export paths
        for i, trench in enumerate(col.trench_list):
            wall_filename = os.path.join(col_dir, f'trench{i+1:03}_wall')
            floor_filename = os.path.join(col_dir, f'trench{i+1:03}_floor')

            # Export wall
            t_gc = PGMCompiler(wall_filename, ind_rif=ind_rif, angle=angle)
            t_gc.export_array(*trench.border, f=tspeed)
            del t_gc

            # Export floor
            t_gc = PGMCompiler(floor_filename, ind_rif=ind_rif, angle=angle)
            t_gc.export_array(*trench.floor, f=tspeed)
            del t_gc

        self.dvar(['ZCURR'])
        for file in glob.glob(os.path.join(col_dir, "*.pgm")):
            lab_filename = os.path.join(base_folder,
                                        trench_directory,
                                        os.path.basename(file))
            self.load(lab_filename)
        self.dwell(pause)

        for nbox in range(nboxz):
            for t_index, trench in enumerate(col.trench_list):
                # load filenames (wall/floor)
                wall_filename = f'trench{t_index+1:03}_wall.pgm'
                floor_filename = f'trench{t_index+1:03}_floor.pgm'

                self.comment(f'+--- TRENCH #{t_index+1}, LEVEL {i+1} ---+')
                self.shutter('OFF')
                x0, y0 = trench.border[:, 0]
                z0 = (nbox*hbox - zoff)/ind_rif
                self.move_to([x0, y0, z0], speed_pos=speed_pos)

                self.instruction(f'$ZCURR = {z0:.6f}')
                self.shutter('ON')
                self.rpt(int(np.ceil((hbox+zoff)/deltaz)))
                self.farcall(wall_filename)
                self.instruction(f'$ZCURR = $ZCURR + {deltaz/ind_rif:.6f}')
                self.instruction('LINEAR Z$ZCURR')
                self.endrpt()

                self.farcall(floor_filename)
                self.shutter('OFF')
                self.dwell(pause)
        for file in glob.glob(os.path.join(col_dir, "*.pgm")):
            self.remove(os.path.basename(file))
        self.dwell(pause)

    def instruction(self, instr: str):
        if instr.endswith('\n'):
            self._instructions.append(instr)
        else:
            self._instructions.append(instr+'\n')

    def export_array(self,
                     x: List = [],
                     y: List = [],
                     z: List = [],
                     f: List = []):
        args = self._format_array(x, y, z, f)
        for line in args:
            self._instructions.append(f'LINEAR {line}\n')
        self.compile_pgm()

    def compile_pgm(self, verbose=False):
        """
        COMPILE PGM.

        The function dumps all the instruction in self._instruction in a .pgm
        file.
        The filename is specified during the class instatiation. If no
        extension is present, the proper one is automatically added.

        Returns
        -------
        None.

        """
        assert self.filename is not None, 'No filename given.'
        assert self._num_repeat == 0, \
            (f'Missing {np.abs(self._num_repeat)} ' +
             f'{"END REPEAT" if self._num_repeat >0 else "REPEAT"} ' +
             f'instruction{"s" if np.abs(self._num_repeat) != 1 else ""}.')
        assert self._num_for == 0, \
            (f'Missing {np.abs(self._num_for)} ' +
             f'{"NEXT" if self._num_for >0 else "FOR"} ' +
             f'instruction{"s" if np.abs(self._num_for) != 1 else ""}.')

        # if not present in the filename, add the proper file extension
        if not self.filename.endswith('.pgm'):
            self.filename += '.pgm'

        # write instruction to file
        with open(self.filename, 'w') as f:
            f.write(''.join(self._instructions))
        if verbose:
            print('G-code compilation completed.')

    # Private interface
    def _compute_t_matrix(self) -> np.ndarray:
        """
        COMPUTE TRANSFORMATION MATRIX.

        Given the rotation angle and the rifraction index, the function
        compute the transformation matrix as composition of rotatio matrix (RM)
        and a homothety matrix (SM).

        Returns
        -------
        np.array
            Transformation matrix: TM = SM*RM

        """
        RM = np.array([[np.cos(self.angle), -np.sin(self.angle), 0],
                       [np.sin(self.angle), np.cos(self.angle), 0],
                       [0, 0, 1]])
        SM = np.array([[1, 0, 0],
                       [0, 1, 0],
                       [0, 0, 1/self.ind_rif]])
        return np.dot(SM, RM)

    def _format_args(self,
                     x: float = None,
                     y: float = None,
                     z: float = None,
                     f: float = None) -> str:
        """
        FORMAT ARGUMENTS.

        Utility function that creates a string prepending the coordinate name
        to the given value for all the given the coordinates (X,Y,Z) and feed
        rate (F).
        The decimal precision can be set by the user by setting the
        output_digits attribute.

        Parameters
        ----------
        x : float, optional
            Value of the X coordinate [mm]. The default is None.
        y : float, optional
            Value of the Y coordinate [mm]. The default is None.
        z : float, optional
            Value of the Z coordinate [mm]. The default is None.
        f : float, optional
            Value of the F rate [mm/s]. The default is None.

        Raises
        ------
        ValueError
            Check F is not 0 mm/s.

        Returns
        -------
        str
            Formatted string of the type:
                'X<value> Y<value> Z<value> F<value>'.

        """

        args = []
        if x is not None:
            args.append('{0}{1:.{digits}f}'.format('X', x,
                                                   digits=self.output_digits))
        if y is not None:
            args.append('{0}{1:.{digits}f}'.format('Y', y,
                                                   digits=self.output_digits))
        if z is not None:
            args.append('{0}{1:.{digits}f}'.format('Z', z,
                                                   digits=self.output_digits))
        if f is not None:
            if f < 1e-6:
                raise ValueError('Try to move with F = 0.0 mm/s.',
                                 'Check speed parameter.')
            args.append('{0}{1:.{digits}f}'.format('F', f,
                                                   digits=self.output_digits))
        args = ' '.join(args)
        return args

    def _format_array(self,
                      x_array: List = [],
                      y_array: List = [],
                      z_array: List = [],
                      f_array: List = []) -> List:

        if not isinstance(x_array, Iterable): x_array = [x_array]
        if not isinstance(y_array, Iterable): y_array = [y_array]
        if not isinstance(z_array, Iterable): z_array = [z_array]
        if not isinstance(f_array, Iterable): f_array = [f_array]

        args_array = []
        for (x, y, z, f) in zip_longest(x_array, y_array, z_array, f_array):
            args_array.append(self._format_args(x, y, z, f))
        return args_array

    def _parse_filepath(self,
                        filename: str,
                        filepath: str = None,
                        extension: str = None) -> Path:
        """
        PARSE FILEPATH.

        The fuction takes a filename and (optional) filepath. It merges the
        two and check if the file exists in the system.
        An extension parameter can be given as input. In that case the
        function also checks weather the filename has the correct extension.

        Parameters
        ----------
        filename : str
            Name of the file that have to be loaded.
        filepath : str, optional
            Path of the folder containing the file. The default is None.
        extension : str, optional
            File extension. The default is None.

        Returns
        -------
        file : pathlib.Path
            Complete path of the file (filepath + filename).

        """
        if extension is not None:
            assert filename.endswith(extension), \
                ('Given filename has wrong extension.' +
                 f'Given {filename}, required .{extension}.')

        if filepath is not None:
            file = Path(filepath) / filename
        else:
            file = Path(filename)
        return file


if __name__ == '__main__':

    from femto.objects import Waveguide

    # Data
    pitch = 0.080
    int_dist = 0.007
    angle = 1
    ind_rif = 1.5/1.33

    d_bend = 0.5*(pitch-int_dist)
    increment = [4, 0, 0]

    # Calculations
    coup = [Waveguide(num_scan=6) for _ in range(2)]
    for i, wg in enumerate(coup):
        wg.start([-2, -pitch/2 + i*pitch, 0.035])
        wg.linear(increment, speed=20)
        wg.sin_mzi((-1)**i*d_bend, radius=15, arm_length=1.0, speed=20, N=50)
        wg.linear(increment, speed=20)
        wg.end()

    # Compilation
    with PGMCompiler('testPGMcompiler', ind_rif=ind_rif, angle=angle) as gc:
        gc.rpt(wg.num_scan)
        for i, wg in enumerate(coup):
            gc.comment(f'Modo: {i}')
            gc.point_to_instruction(wg.M)
        gc.endrpt()
        gc.move_to([None, 0, 0.1])
        gc.set_home([0, 0, 0])
        gc.homing()
