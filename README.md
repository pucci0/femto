<div id="top"></div>


<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://user-images.githubusercontent.com/45992199/205449527-d349ee82-39fb-4e1f-b25b-dbd2260ad9a4.svg" width="480">
    <img alt="Shows an illustrated sun in light color mode and a moon with stars in dark color mode." src="https://user-images.githubusercontent.com/45992199/205449385-341529d6-0575-430c-a0b4-62f50579db19.svg" width="480">
  </picture>
</div>  

<p align="center"> 
  <br>
  <br>
  Python library for the design of femtosecond laser-written integrated photonic circuits.
</p>
  

<div align="center">

  ![Tests](https://github.com/ricalbr/femto/actions/workflows/tests.yml/badge.svg)
  
</div>

## <img src="https://mir-s3-cdn-cf.behance.net/project_modules/disp/511fdf30195555.560572b7c51e9.gif" alt="femto logo" width="32"> Table of Contents

* [Installation](#installation)
* [Features](#features)
* [Usage](#usage)
* [Issues](#issues)
<!-- * [License](#license) -->


## Installation
The preferred way to install `femto` is using a conda virtual environment. From the conda/Anaconda prompt
```bash
conda update conda
conda create --name femto python=3.8
conda activate femto
```
`femto` supports all the versions of Python from 3.7. 

The package can be installed using `pip`
```bash
git clone git@github.com:ricalbr/femto.git
cd femto
pip install -e .
```
Alternatively, the repository can also be cloned directly from Gihub website.
To install `pip` follow [this](https://pip.pypa.io/en/stable/installation/) guide.

<p align="right">(<a href="#top">back to top</a>)</p>

## Features

`femto` is an open-source package for the design of integrated optical circuits. 
The library consists of a growing list of parts and modules, which can be composed to construct complex optical components and large circuits.
The optical components can be plotted and exported to a .pgm file for the fabrication of the circuit. 

The following optical components are implemented:

* Multiscan and Nasu Waveguide class, allowing easy chaining of bends and straight segments. Including:
    * Circular and sinusoidal arcs
    * Directional couplers
    * Mach-Zehnder interferometers
    * Spline segments and 3D bridges
* Trench class, allowing easy generation of complex toolpaths for arbitrary geometries
* Marker and superficial ablation class
* A possibility to include images

The G-Code file is generated by a dedicated compiler class that allows performing various geometric transformations on the optical components like:

* Translations
* Rotations
* Homothetic transformations, for refractive index change compensation
* Flip transformations along x- or y-axis
* Compensation for sample warp

The different structures can be organized into single cell-like objects, which allow:

* Collecting different structures of a circuit
* 2D and 3D representations of the stored objects
* Automatized generation of G-Code files
* Export plots in different formats

<p align="right">(<a href="#top">back to top</a>)</p>


## Usage

Here a brief example on how to use the library.

First, import all the required modules

```python
from femto.device import Device
from femto.pgmcompiler import PGMCompiler
from femto.waveguide import Waveguide
```

Define a `Device` which represents a circuit

```python
circuit = Device()
```

Set waveguide parameters

```python
PARAM_WG = dict(scan=4,
                speed=7.5,
                depth=0.050,
                radius=25)
```

Create a list of waveguides as

```python
wgs = []

# SWG
wg = Waveguide(**PARAM_WG)
wg.start([-2, 4.5, 0.050])
wg.linear([27, 4.5, 0.050], mode='ABS')
wg.end()
wgs.append(wg)

# MZI
for i in range(6):
    wg = Waveguide(**PARAM_WG)
    wg.start([-2, 5+i*0.080, 0.500])
    wg.linear([10, 0, 0], mode='INC')
    wg.arc_mzi((-1)**i * 0.037)
    wg.linear([27, 5+i*0.080, 0.500], mode='ABS')
    wg.end()
    wgs.append(wg)
```

Now that the waveguides are defined we set the fabrication parameters

```python
PARAM_GC = dict(filename='MZIs.pgm',
                laser='PHAROS',
                samplesize=(25, 10),
                rotation_angle=0.0)
```

Create a `Device` object that allows to store the waveguides and plot them
```python
# CIRCUIT
circuit = Device(**PARAM_GC)

# Add waveguides to circuit
circuit.extend(wgs)

# Make a plot of the circuit
circuit.plot2d()
```

Export the G-Code with the following commands

```python
# Export G-Code file
with PGMCompiler(**PARAM_GC) as G:
    G.tic()
    with G.repeat(6):
        for i, wg in enumerate(wgs):
            G.comment(f' +--- Mode: {i + 1} ---+')
            G.write(wg.points)
    G.toc()
    G.go_origin()

```

Other example files can be found [here](https://github.com/ricalbr/femto/tree/main/examples)

<p align="right">(<a href="#top">back to top</a>)</p>

## Issues

To request features or report bugs open an issue [here](https://github.com/ricalbr/femto/issues)

<p align="right">(<a href="#top">back to top</a>)</p>

