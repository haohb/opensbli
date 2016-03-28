""" Reads in the 1D viscous Burgers solution data, written by the OPS dat writer,
and plots the scalar field 'phi'. """

import argparse
import numpy
from math import pi, exp, cos, sin
import matplotlib.pyplot as plt
import h5py

def plot(path):
    # Number of grid points
    nx = 200
    
     # Number of halo nodes at each end
    halo = 2

    # Read in the simulation output
    f = h5py.File(path + "/state.h5", 'r')
    group = f["viscous_burgers_block"]
    
    phi = group["phi"].value
    
    # Ignore the 2 halo nodes at either end of the domain
    phi = phi[halo:nx+halo]
    print phi
    # Grid spacing
    dx = 1.0/(nx);
    
    # Coordinate array
    x = numpy.zeros(nx)
    phi_analytical = numpy.zeros(nx)
    phi_error = numpy.zeros(nx)

    # Compute the error
    for i in range(0, nx):
        x[i] = i*dx

    plt.plot(x, phi, label=r"Solution field $\phi$")
    plt.xlabel(r"$x$ (m)")
    plt.ylabel(r"$\phi$")
    plt.legend()
    plt.savefig("phi.pdf")
    


if(__name__ == "__main__"):
    # Parse the command line arguments provided by the user
    parser = argparse.ArgumentParser(prog="plot")
    parser.add_argument("path", help="The path to the directory containing the output files.", action="store", type=str)
    args = parser.parse_args()
    
    plot(args.path)
