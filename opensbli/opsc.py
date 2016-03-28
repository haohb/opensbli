#!/usr/bin/env python

#    OpenSBLI: An automatic code generator for solving differential equations.
#    Copyright (C) 2016 Satya P. Jammy, Christian T. Jacobs

#    This file is part of OpenSBLI.

#    OpenSBLI is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    OpenSBLI is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.

#    You should have received a copy of the GNU General Public License
#    along with OpenSBLI.  If not, see <http://www.gnu.org/licenses/>.

from sympy import *
from sympy.printing.ccode import CCodePrinter
from sympy.parsing.sympy_parser import parse_expr
import re
import os
from string import Template

import logging
LOG = logging.getLogger(__name__)
BUILD_DIR = os.getcwd()


class OPSCCodePrinter(CCodePrinter):
    
    """ Prints OPSC code. """

    def __init__(self, Indexed_accs, constants):
        """ Initialise the code printer. """
    
        settings = {}
        CCodePrinter.__init__(self, settings)
        
        # Indexed access numbers are required in dictionary
        self.Indexed_accs = Indexed_accs
        self.constants = constants
        
    def _print_Rational(self, expr):
        """ Print a Rational expression as a literal division.
        
        :arg expr: The Rational expression.
        :returns: The rational expression, as OPSC code, represented by a literal division of two integers.
        :rtype: str
        """
        p, q = int(expr.p), int(expr.q)
        return '%d.0/%d.0' %(p,q)
        
    def _print_Indexed(self, expr):
        """ Print out an Indexed object.
        
        :arg expr: The Indexed expression.
        :returns: The indexed expression, as OPSC code.
        :rtype: str
        """
    
        # Find the symbols in the indices of the expression
        symbols = flatten([list(index.atoms(Symbol)) for index in expr.indices])
        
        # Replace the symbols in the indices with `zero'
        for x in symbols:
            expr = expr.subs({x: 0})
            
        if self.Indexed_accs[expr.base]:
            out = "%s[%s(%s)]" % (self._print(expr.base.label), self.Indexed_accs[expr.base], ','.join([self._print(index) for index in expr.indices]))
        else:
            out = "%s[%s]" % (self._print(expr.base.label), ','.join([self._print(index) for index in expr.indices]))
            
        return out

def ccode(expr, Indexed_accs=None, constants=None):
    """ Create an OPSC code printer object and write out the expression as an OPSC code string.
    
    :arg expr: The expression to translate into OPSC code.
    :arg Indexed_accs: Indexed OPS_ACC accesses.
    :arg constants: Constants that should be defined at the top of the OPSC code.
    :returns: The expression in OPSC code.
    :rtype: str
    """
    if isinstance(expr, Eq):
        # If the expression is a SymPy Eq object, then write the LHS and the RHS with an equals sign in between.
        return OPSCCodePrinter(Indexed_accs, constants).doprint(expr.lhs) + ' = ' + OPSCCodePrinter(Indexed_accs, constants).doprint(expr.rhs)
    return OPSCCodePrinter(Indexed_accs, constants).doprint(expr)

class OPSC(object):

    """ A class describing the OPSC language, and various templates for OPSC code structures (e.g. loops, declarations, etc). """

    # OPS Access types, used for kernel call
    ops_access = {'inputs':'OPS_READ', 'outputs':'OPS_WRITE', 'inputoutput':'OPS_RW'}
    # OPS kernel headers
    ops_header = {'inputs':'const %s *%s', 'outputs':'%s *%s', 'inputoutput':'%s *%s', 'Idx':'const int *%s'}
    # Single line comment
    line_comment = "//"
    # Block/multi-line comment
    block_comment = ['/*','*/']
    # End of statement delimiter
    end_of_statement = ";"
    # Commonly used brackets
    left_brace = "{"; right_brace = "}"
    left_parenthesis = "("; right_parenthesis = ")"
    
    def __init__(self, grid, spatial_discretisation, temporal_discretisation, boundary, initial_conditions, IO, simulation_parameters, diagnostics=None):
        self.check_consistency(grid, spatial_discretisation, temporal_discretisation, boundary, initial_conditions, IO)
        self.simulation_parameters = simulation_parameters
        # Update the simulation parameters from that of the grid
        for g in self.grid:
            self.simulation_parameters.update(g.grid_data_dictionary)
        self.initialise_ops_parameters()
        self.template()
        return
        
    def initialise_ops_parameters(self):
        """ This initialises various OPS parameters like the name of the computational files,
        computation kernel name, iteration range, stencil name, etc. Most of these are specific to OPS.
        """

        # Multiblock or single block?
        if len(self.grid) == 1:
            self.multiblock = False
            self.nblocks = 1
        else:
            self.multiblock = True
            self.nblocks = len(self.grid)
            
        # Dimensions of the blocks
        ndim = list(set([len(self.grid[i].shape) for i in range(self.nblocks)]))
        if len(ndim) != 1:
            raise ValueError("Mismatch in the grid shape of the blocks.")
        self.ndim = ndim[0]
        name = self.simulation_parameters["name"]
        
        # Name of the block for OPSC
        self.block_name = '%s_block' % name

        # File names for computational kernels, each block will have its own computational kernel file
        self.computational_routines_filename = ['%s_block_%d_kernel.h' % (name, block) for block in range(self.nblocks)]

        # Kernel names for each block. This will be the file name + the kernel number
        self.computational_kernel_names = ['%s_block%d_%%d_kernel' % (name, block) for block in range(self.nblocks)]

        # Name for exchange boundary condition, stencil, iteration range and kernel name
        self.iteration_range_name = 'iter_range%d'
        
        # Name for the commonly-used stencils
        self.stencil_name = 'stencil%d'
        self.stencil_number = 0
        self.iteration_range_index = 0
        self.kernel_name_number = [0 for block in range(self.nblocks)]

        # Name for exchange boundary conditions
        self.halo_exchange_number = 0
        self.halo_exchange_name = 'halo_exchange%d'
        # Implicit from the descritisations
        # Grid based arrays used for declaration and definition in OPSC format
        self.grid_based_arrays = set()
        # The global constants that are to be declared.
        self.constants = set()
        # OPS constants. These are the constants in the above list to be defined in OPS format.
        self.constant_values = {}
        self.rational_constants = {}

        # Dictionary of stencils. The key will be a stencil, and the value is the name of stencil.
        self.stencil_dictionary = {}

        # Data type of arrays
        self.dtype = self.simulation_parameters['precision']

        # Create the code directory
        if not os.path.exists(BUILD_DIR+'/%s_opsc_code' % name):
            os.makedirs(BUILD_DIR+'/%s_opsc_code' % name)
        self.CODE_DIR = BUILD_DIR + '/%s_opsc_code' % name
        return
        
    def template(self):
        """ Define the algorithm in pseudo-code and get all the code. """

        OPS_template \
        ='''$header
        \n$main_start
            \n$initialise_constants
            \n$ops_init
            \n$declare_ops_constants
            \n$define_block
            \n$initialise_block
            \n$define_dat
            \n$initialise_dat
            \n$declare_stencils
            \n$bc_exchange
            \n$ops_partition
            \n$initialisation
            \n$bc_calls
            \n$timer_start
                \n$timeloop
                    \n$time_start_calls
                        \n$innerloop
                            \n$time_calls
                            \n$bc_calls
                        \n$end_inner_loop
                    \n$time_end_calls
                    \n$io_time
                \n$end_time_loop
            \n$timer_end
            \n$print_timings
            \n$io_calls
            \n$ops_exit
        \n$main_end
        '''
        # OPS template is written in multiple lines for clarity, remove all white spaces for nicety of file printing
        OPS_template = OPS_template.replace(" ","")
        # Dictionary to store evaluated things in the code_template
        code_dictionary = {}
        # Conver OPS_template to a python Template
        code_template = Template(OPS_template)

        # start populating the code dictionary with the corresponding code

        # get the main start and main end code
        code_dictionary['main_start'] = '\n'.join(self.main_start())
        code_dictionary['main_end'] = self.right_brace

        # get the ops init, ops_exit (footer) calls
        code_dictionary['ops_init'] = '\n'.join(self.ops_init())
        code_dictionary['ops_exit'] = '\n'.join(self.footer())
        code_dictionary['ops_partition'] = '\n'.join(self.ops_partition())

        # set the timers code
        timer = self.ops_timers()
        code_dictionary['timer_start'] = '\n'.join(timer[0])
        code_dictionary['timer_end'] = '\n'.join(timer[1])
        code_dictionary['print_timings'] = '\n'.join(timer[2])

        # set the main time loop
        var = 'iteration' # name for iteration
        code_dictionary['timeloop'] = self.loop_open(var,(0,self.simulation_parameters['niter'])) + '\n'
        code_dictionary['end_time_loop'] = self.loop_close()

        # declare and initialise OPS block
        code_dictionary['define_block'] = '\n'.join(self.define_block())
        code_dictionary['initialise_block'] = '\n'.join(self.initialise_block())

        # Commonly used Stencils
        #code_dictionary['define_stencils'] = '\n'.join()

        # Set the time sub loop if exist
        if self.temporal_discretisation[0].nstages >1:
            ns = self.temporal_discretisation[0].nstages
            var = self.temporal_discretisation[0].scheme.stage
            code_dictionary['innerloop'] = self.loop_open(var,(0,ns))+ '\n'
            code_dictionary['end_inner_loop'] = self.loop_close()
            # update constant values
            coeffs = self.temporal_discretisation[0].scheme.get_coefficients()
            for key, value in coeffs.iteritems():
                self.simulation_parameters[str(key)] = value
        else:
            code_dictionary['innerloop'] = ''
            code_dictionary['end_inner_loop'] = ''

        # Get the computational routines
        computational_routines = self.get_block_computations()
        # write the computational routines to block computation files
        self.write_computational_routines(computational_routines)

        # computation calls
        # First the inner computation calls
        computations = [self.spatial_discretisation[block].computations + self.temporal_discretisation[block].computations\
            for block in range(self.nblocks)]
        calls = self.get_block_computation_kernels(computations)
        code_dictionary['time_calls'] = '\n'.join(['\n'.join(calls[block]) for block in \
            range(self.nblocks)])
        # Computations at the start of the time stepping loop
        computations = [self.temporal_discretisation[block].start_computations if self.temporal_discretisation[block].start_computations\
            else [] for block in range(self.nblocks)]
        calls = self.get_block_computation_kernels(computations)
        code_dictionary['time_start_calls'] = '\n'.join(['\n'.join(calls[block]) for block in \
            range(self.nblocks)])

        # computations at the end of the time stepping loop
        computations = [self.temporal_discretisation[block].end_computations if self.temporal_discretisation[block].end_computations\
            else [] for block in range(self.nblocks)]
        calls = self.get_block_computation_kernels(computations)
        code_dictionary['time_end_calls'] = '\n'.join(['\n'.join(calls[block]) for block in \
            range(self.nblocks)])

        # computations for the initialisation, if there are computations, later this should be included to read from file
        computations = [self.initial_conditions[block].computations if self.initial_conditions[block].computations\
            else [] for block in range(self.nblocks)]
        calls = self.get_block_computation_kernels(computations)
        code_dictionary['initialisation'] = '\n'.join(['\n'.join(calls[block]) for block in \
            range(self.nblocks)])

        # Do the exchange boundary conditions
        code_dictionary = self.update_boundary_conditions(code_dictionary)

        # Update the IO calls
        code_dictionary = self.get_io(code_dictionary)

        # define and initialise all the data arrays used in the computations
        code_dictionary['define_dat'] = '\n'.join(self.define_dat())
        code_dictionary['initialise_dat'] = '\n'.join(self.initialise_dat())

        # header
        code_dictionary['header'] = '\n'.join(self.header())
        # Stencils
        code_dictionary['declare_stencils'] = '\n'.join(self.declare_stencils())

        # Initialise constants and Declare  constants in OPS format
        code_dictionary['initialise_constants'] = '\n'.join(self.initialize_constants())
        code_dictionary['declare_ops_constants'] = '\n'.join(self.declare_ops_constants())
        # write the main file
        code_template = code_template.safe_substitute(code_dictionary)
        self.write_main_file(code_template)
        return
    def initialize_constants(self):
        '''
        '''
        # Get the constants defined every where. i.e. in
        const_init = []
        for con in self.constants:
            val = self.simulation_parameters[str(con)]
            if isinstance(con, IndexedBase):
                if con.ranges != len(val):
                    raise ValueError("The indexed constant %s should have only %d values"\
                        %(con,con.ranges))
                for r in range(con.ranges):
                    const_init += ["%s[%d] = %s%s"%(con, r, ccode(val[r]), self.end_of_statement)]
            else:
                const_init += ["%s = %s%s"%(con, ccode(val), self.end_of_statement)]
        return const_init
    def declare_ops_constants(self):
        ops_const = []
        for con in self.constants:
            if not isinstance(con, IndexedBase):
                ops_const += ["ops_decl_const(\"%s\" , 1, \"%s\", &%s)%s"%(con, self.dtype, con, self.end_of_statement)]
        return ops_const
    def write_main_file(self, code_template):
        mainfile = open(self.CODE_DIR+'/'+'%s.cpp'%self.simulation_parameters["name"], 'w')
        code_template = self.indent_code(code_template)
        mainfile.write(code_template)
        mainfile.close()
        return
    def indent_code(self, code_lines):
        p = CCodePrinter()
        return p.indent_code(code_lines)
    def update_boundary_conditions(self, code_dictionary):
        bc_call = [[] for block in range(self.nblocks)]
        bc_exchange_code = [[] for block in range(self.nblocks)]
        for block in range(self.nblocks):
            boundary_instance = self.boundary[block]
            for instance in range(len(self.boundary[block].type)):
                if boundary_instance.type[instance] == 'exchange_self':
                    call, code = self.bc_exchange_call_code(boundary_instance.transfers[instance])
                    bc_call[block] += call
                    bc_exchange_code[block] += code
                else:
                    raise NotImplementedError("Only boundary conditions of type exchange are supported")
        code_dictionary['bc_exchange'] = '\n'.join(['\n'.join(bc_exchange_code[block]) for block in \
            range(self.nblocks)])
        code_dictionary['bc_calls'] = '\n'.join(['\n'.join(bc_call[block]) for block in \
            range(self.nblocks)])
        return code_dictionary
    def get_io(self, code_dictionary):
        '''
        As of now only FileIO at the end of the simulation is performed, no intermediate saves are allowed
        '''
        io_calls = [[] for block in range(self.nblocks)]
        io_time = [[] for block in range(self.nblocks)]
        for block in range(self.nblocks):
            # Process FileIo
            save_at = self.IO[block].save_after
            if len(save_at) == 1 and save_at[0]== True:
                io_calls[block] += self.HDF5_array_fileIO(self.IO[block])
            else:
                raise NotImplementedError("Implement IO at time steps ")
        code_dictionary['io_calls'] = '\n'.join(['\n'.join(io_calls[block]) for block in \
            range(self.nblocks)])
        code_dictionary['io_time'] = '\n'.join(['\n'.join(io_time[block]) for block in \
            range(self.nblocks)])
        return code_dictionary
    def get_block_computation_kernels(self, instances):
        '''

        '''
        # First process the inner time calls
        calls = [[] for block in range(self.nblocks)]
        for block in range(self.nblocks):
            for instance in instances[block]:
                if instance:
                    calls[block] += self.kernel_call(instance)
        return calls

    def kernel_call(self, computation):
        iterrange = self.iteration_range_name%self.iteration_range_index
        self.iteration_range_index = self.iteration_range_index +1
        stencils = self.get_stencils(computation)
        kercall = []
        # range of iterations
        range_main =  self.array('int', iterrange, [r for ran in computation.ranges for r in ran])

        kercall += ['ops_par_loop(%s, \"%s\", %s, %s, %s' % (computation.name,\
            computation.computation_type,self.block_name, self.ndim, iterrange)]

        # do the inputs first grid based
        grid_based = [self.ops_argument_call(inp, stencils[inp],self.dtype, self.ops_access['inputs'])\
            for inp in computation.inputs.keys() if inp.is_grid ] + \
                [self.ops_argument_call(inp, stencils[inp],self.dtype, self.ops_access['outputs'])\
                    for inp in computation.outputs.keys() if inp.is_grid ] + \
                        [self.ops_argument_call(inp, stencils[inp],self.dtype, self.ops_access['inputoutput'])\
                            for inp in computation.inputoutput.keys() if inp.is_grid ]
        # Globals
        nongrid = [self.ops_global_call(inp, value, self.dtype, self.ops_access['inputs'])\
            for inp,value in computation.inputs.iteritems() if not inp.is_grid ] + \
                [self.ops_global_call(inp, value, self.dtype, self.ops_access['outputs'])\
                    for inp, value in computation.outputs.iteritems() if not inp.is_grid ] + \
                        [self.ops_global_call(inp, value, self.dtype, self.ops_access['inputoutput'])\
                            for inp, value in computation.inputoutput.iteritems() if not inp.is_grid ]
        if computation.has_Idx:
            nongrid += [self.grid_index_call()]
        kercall = kercall + grid_based + nongrid
        call = [k+',' for k in kercall[:-1]]
        call = [range_main] + call + [kercall[-1] + self.right_parenthesis + self.end_of_statement] + ['\n']
        return call

    def get_stencils(self, computation):
        stencils = {}
        dicts = [computation.inputs, computation.outputs,computation.inputoutput]
        for d in dicts:
            for key, value in d.iteritems():
                if key.is_grid:
                    sten = self.relative_stencil(value)
                    stencil = self.lsit_to_string(sten)
                    if stencil not in self.stencil_dictionary.keys():
                        self.stencil_dictionary[stencil] = self.stencil_name%self.stencil_number
                        self.stencil_number = self.stencil_number +1
                    # Update the stencils to be returned
                    stencils[key] = self.stencil_dictionary[stencil]
        return stencils
    def lsit_to_string(self, inlist):
        string = ','.join([str(s) for s in inlist])
        return string
    def relative_stencil(self, value):
        '''
        Helper function for get stencils
        This returns the relative stencil wrt the grid location
        i.e. grid indices eg(i0,i1,i2) are replaced with (0,0,0)
        TODO Need to check if OPS also requires the grid location

        '''
        if isinstance(value,list):
            pass
        else:
            value = [value]
        retun_val = []
        for va in value:
            out = []
            for number, v in enumerate(va):
                outv = v
                for a in v.atoms(Symbol):
                    outv = outv.subs(a,0)
                out.append(outv)
            retun_val.append(out)
        retun_val = self.sort_stencil_indices(retun_val)

        return retun_val
    def sort_stencil_indices(self, indexes):
        '''
        helper function for relative_stencil, sorts the relative stencil
        '''
        if len(indexes[0]) > 1:
            for dim in range(len(indexes[0])):
                indexes = sorted(indexes, key=lambda indexes: indexes[dim])
            temp = flatten(list(list(t) for t in indexes))
        else:
            indexes = [sorted(indexes)]
            temp = flatten(list(t) for t in indexes)
        return temp

    def grid_index_call(self):
        return 'ops_arg_idx()'
    def ops_global_call(self, array, indices, precision, access_type):
        arr = array[tuple(indices[0])]
        template = 'ops_arg_gbl(&%s, %d, \"%s\", %s)'
        return template%(arr,1, self.dtype, access_type)
    def ops_argument_call(self, array, stencil, precision, access_type):
        template = 'ops_arg_dat(%s, %d, %s, \"%s\", %s)'
        return template%(array,1,stencil, self.dtype, access_type)
    def bc_exchange_call_code(self, instance):
        off = 0; halo = 'halo'
        #name of the halo exchange
        name = self.halo_exchange_name%(self.halo_exchange_number)
        self.halo_exchange_number = self.halo_exchange_number +1
        code = ['%s Boundary condition exchange code'%self.line_comment]
        code += ['ops_halo_group %s %s'%(name, self.end_of_statement)]
        code += [self.left_brace]
        code += ['int halo_iter[] = {%s}%s'%(', '.join([str(s) for s in instance.transfer_size]), self.end_of_statement)]
        code += ['int from_base[] = {%s}%s'%(', '.join([str(s) for s in instance.transfer_from]), self.end_of_statement)]
        code += ['int to_base[] = {%s}%s'%(', '.join([str(s) for s in instance.transfer_to]), self.end_of_statement)]
        # dir in OPSC not sure what it is but 1to ndim works
        code += ['int dir[] = {%s}%s'%(', '.join([str(ind+1) for ind in range(len(instance.transfer_to))]), self.end_of_statement)]
        # now process the arrays
        for arr in instance.transfer_arrays:
            code += ['ops_halo %s%d = ops_decl_halo(%s, %s, halo_iter, from_base, to_base, dir, dir)%s'\
                %(halo, off, arr, arr, self.end_of_statement)]
            off = off+1
        code += ['ops_halo grp[] = {%s}%s'%(','.join([str('%s%s'%(halo, of)) for of in range(off)]),self.end_of_statement )]
        code += ['%s = ops_decl_halo_group(%d,grp)%s'%(name, off, self.end_of_statement)]
        code += [self.right_brace]
        # finished OPS halo exchange, now get the call
        call = ['%s Boundary condition exchange calls'%self.line_comment,'ops_halo_transfer(%s)%s'%(name,self.end_of_statement)]
        return call, code

    def initialise_dat(self):
        code = ['%s Initialize/ Allocate data files'%(self.line_comment)]
        dtype_int = 'int'
        if not self.multiblock:
            grid = self.grid[0]
            code += [self.array(dtype_int, 'halo_p', [halo[1] for halo in grid.halos])]
            code += [self.array(dtype_int, 'halo_m', [halo[0] for halo in grid.halos])]
            code += [self.array(dtype_int, 'size', grid.shape)]
            code += [self.array(dtype_int, 'base', [0 for g in grid.shape])]
            code += ['%s* val = NULL;'%(self.dtype)]
            init_format = '%%s = ops_decl_dat(%s, 1, size, base, halo_m, halo_p, val, \"%%s\", \"%%s\")%s'\
            % (self.block_name, self.end_of_statement)
            inits = [init_format%(arr, self.dtype, arr) for arr in self.grid_based_arrays]
            code = code + inits
        else:
            raise NotImplementedError("Multi block is not implemented")
        return code
    def declare_stencils(self):
        '''
        This declares all the stencils used in the code.
        We donot differentiate between the stencils for each block.
        returns the code
        '''
        code = ['%s Declare all the stencils used '%(self.line_comment)]
        dtype_int = 'int'
        sten_format = 'ops_stencil %%s = ops_decl_stencil(%%d,%%d,%%s,\"%%s\")%s'%(self.end_of_statement)
        for key, value in self.stencil_dictionary.iteritems():
            count = len(key.split(','))/ self.ndim
            # value is the name in the stencils format
            code += [self.array(dtype_int, value + "_temp", [key])]
            code += [sten_format%(value, self.ndim, count, value + "_temp", key)]
        return code
    def HDF5_array_fileIO(self,instance):
        code = []
        # to do generate file name automatically
        block_to_hdf5 = ["ops_fetch_block_hdf5_file(%s, \"state.h5\")%s" % (self.block_name, self.end_of_statement)]
        code += block_to_hdf5
        # Then write out each field.
        for c in instance.save_arrays:
            variables_to_hdf5 = ["ops_fetch_dat_hdf5_file(%s, \"state.h5\")%s" \
                % (c, self.end_of_statement)]
            code += variables_to_hdf5
        return code

    def get_block_computations(self):
        '''
        This gets all the block computations to be performed.
        Extra stuff like diagnostic computations or BC computations should be
        added here.
        '''
        kernels = [[] for block in range(self.nblocks)]
        for block in range(self.nblocks):
            # get all the computations to be performed, add computations as needed
            block_comps = []
            if self.spatial_discretisation[block].computations:
                block_comps += self.spatial_discretisation[block].computations
            if self.initial_conditions[block].computations:
                block_comps += self.initial_conditions[block].computations
            if self.temporal_discretisation[block].computations:
                block_comps += self.temporal_discretisation[block].computations
            if self.temporal_discretisation[block].start_computations:
                block_comps += self.temporal_discretisation[block].start_computations
            if self.temporal_discretisation[block].end_computations:
                block_comps += self.temporal_discretisation[block].end_computations

            for comp in block_comps:
                kernels[block] += self.kernel_computation(comp,block)
        return kernels
    def kernel_computation(self, computation, block_number):
        '''
        This generates the computation kernel for the computation
        This acts as a helper function for the block computations
        '''
        header = []
        comment_eq = [self.block_comment[0]]
        # Flops count for grid point
        count = sum([count_ops(eq.rhs) for eq in computation.equations])
        # Flops count for the entire grid
        rang = [(ran[1]- ran[0]) for ran in computation.ranges]
        gridcount = count
        for r in rang:
            gridcount = gridcount*r

        for eq in computation.equations:
            comment_eq += [pretty(eq,use_unicode=False)]
        comment_eq += ['The count of operations per grid point for the kernel is %d'%count]
        comment_eq += ['The count of operations on the range of evaluation for the kernel is %d'%gridcount]
        comment_eq += [self.block_comment[1]]

        if computation.name == None:
            computation.name = self.computational_kernel_names[block_number]%self.kernel_name_number[block_number]

        # process inputs
        grid_based = ([self.ops_header['inputs']%(self.dtype,inp) for inp in computation.inputs.keys() if inp.is_grid] + \
            [self.ops_header['outputs']%(self.dtype,inp) for inp in computation.outputs.keys() if inp.is_grid ] + \
                [self.ops_header['inputoutput']%(self.dtype,inp) for inp in computation.inputoutput.keys() if inp.is_grid])

        # nongrid based inputs are
        nongrid = ([self.ops_header['inputs']%(self.dtype,inp) for inp in computation.inputs.keys() if not inp.is_grid] + \
            [self.ops_header['outputs']%(self.dtype,inp) for inp in computation.outputs.keys() if not inp.is_grid ] + \
                [self.ops_header['inputoutput']%(self.dtype,inp) for inp in computation.inputoutput.keys() if not inp.is_grid])

        header += grid_based + nongrid

        if computation.has_Idx:
            header += [self.ops_header['Idx']%('idx') ]
        header = comment_eq + ['void ' + computation.name + self.left_parenthesis + ' , '.join(header) + self.right_parenthesis ]
        header += [self.left_brace]
        code =  header
        ops_accs = self.get_OPS_ACC_number(computation)
        for eq in computation.equations:
            code += [ccode(eq,ops_accs, self.rational_constants)+ self.end_of_statement]
        code += [self.right_brace] + ['\n']
        self.update_definitions(computation)
        # update the kernal name index
        self.kernel_name_number[block_number] = self.kernel_name_number[block_number]+1
        return code
    '''
    Writing the code
    '''
    def write_computational_routines(self, kernels):
        '''
        Writes the computational routines to files.
        '''
        for block in range(self.nblocks):
            code_lines = ["#ifndef block_%d_KERNEL_H"%block + '\n' + "#define block_%d_KERNEL_H"%block + '\n']
            code_lines += kernels[block]
            code_lines += ["#endif"]
            #code_lines  = self.indent_code(code_lines)

            kernel_file = open(self.CODE_DIR+'/'+self.computational_routines_filename[block], 'w')
            kernel_file.write('\n'.join(code_lines))
            kernel_file.close()
        return
    '''
    Some ops stuff
    '''
    def loop_open(self, var, range_of_loop):
        return 'for (int %s=%d; %s<%d; %s++)%s'%(var, range_of_loop[0], var, range_of_loop[1], var,\
            self.left_brace)
    def loop_close(self):
        return self.right_brace
    def header(self):
        code = []
        code += ['#include <stdlib.h>']
        code += ['#include <string.h>']
        code += ['#include <math.h>']
        code += ['%s Global Constants in the equations are' % self.line_comment]
        for con in self.constants:
            if isinstance(con, IndexedBase):
                code += ['%s %s[%d]%s'%(self.dtype, con, con.ranges, self.end_of_statement)]
            else:
                code += ['%s %s%s'%(self.dtype, con, self.end_of_statement)]
        # Include constant declaration
        code += ['// OPS header file']
        code += ['#define OPS_%sD' % self.ndim]
        code += ['#include "ops_seq.h"']
        # Include the kernel file names
        code += ['#include "%s"'%name for name in self.computational_routines_filename]
        return code
    def main_start(self):
        return ['%s main program start' % self.line_comment, 'int main (int argc, char **argv) ', self.left_brace]
    def ops_init(self, diagnostics_level=None):
        '''
        the default diagnostics level in 1 which is the best performance
        refer to ops user manual
        '''
        out = ['%s Initializing OPS '%self.line_comment]
        if diagnostics_level:
            self.ops_diagnostics = True
            return out + ['ops_init(argc,argv,%d)%s'%(diagnostics_level, self.end_of_statement)]
        else:
            self.ops_diagnostics = False
            return out + ['ops_init(argc,argv,%d)%s'%(1, self.end_of_statement)]
    def ops_diagnostics(self):
        '''
        untested OPS diagnostics output need to check if it gives the result or not
        '''
        if self.ops_diagnostics:
            return ['ops diagnostic output()']
        else:
            return []
        return
    def ops_partition(self):
        return ['%s Init OPS partition'%self.line_comment,'ops_partition(\" \")%s' % self.end_of_statement]
    '''
    Timer stuff of OPS
    '''
    def ops_timers(self):
        st = ["cpu_start", "elapsed_start"]
        en = ["cpu_end", "elapsed_end"]
        timer_start = ["double %s, %s%s"%(st[0],st[1],self.end_of_statement)]\
            + ["ops_timers(&%s, &%s)%s"%(st[0], st[1], self.end_of_statement)]
        timer_end = ["double %s, %s%s"%(en[0],en[1],self.end_of_statement)]\
            + ["ops_timers(&%s, &%s)%s"%(en[0], en[1], self.end_of_statement)]
        timing_eval = self.ops_print_timings(st, en)
        return timer_start, timer_end, timing_eval
    def ops_print_timings(self, st, en):
        code = []
        code += ["ops_printf(\"\\nTimings are:\\n\")%s"%self.end_of_statement]
        code += ["ops_printf(\"-----------------------------------------\\n\")%s"%self.end_of_statement]
        code += ["ops_printf(\"Total Wall time %%lf\\n\",%s-%s)%s"%(en[1], st[1],self.end_of_statement)]
        return code
    def define_block(self):
        code = ['%s Defining block in OPS Format'%(self.line_comment)]
        if not self.multiblock:
            # no dynamic memory allocation required
            code += ['ops_block  %s%s'\
            % (self.block_name, self.end_of_statement)]
        else:
            code += ['ops_block *%s = (ops_block *)malloc(%s*sizeof(ops_block*))%s'\
                % (self.block_name, self.nblocks, self.end_of_statement )]
        #print('\n'.join(code))
        return code
    def initialise_block(self):
        code = ['%s Initialising block in OPS Format'%(self.line_comment)]
        if not self.multiblock:
            code += ['%s = ops_decl_block(%d, \"%s\")%s'\
                %(self.block_name,self.ndim, self.block_name,self.end_of_statement)]
        else:
            raise NotImplementedError("Multi block is not implemented")
        #print('\n'.join(code))
        return code
    def define_dat(self):
        code = ['%s Define data files'%(self.line_comment)]
        if not self.multiblock:
            def_format = 'ops_dat %%s%s'% self.end_of_statement
            code += [def_format%arr for arr in self.grid_based_arrays]
        else:
            raise NotImplementedError("Multi block is not implemented")
        return code
    def ops_exit(self):
        '''
        helper function for footer code
        '''
        return ['%s Exit OPS '%self.line_comment,'ops_exit()%s' % self.end_of_statement]
    def footer(self):
        '''
        This writes out the footer code in OPSC this is a call to OPS_exit
        '''
        code = self.ops_exit()
        return code
    def check_consistency(self,  grid, spatial_discretisation, temporal_discretisation, boundary, initial_conditions, IO):
        '''
        Checks the consistency of the inputs
        '''
        self.grid = self.listvar(grid)
        length = len(self.grid)

        self.spatial_discretisation = self.listvar(spatial_discretisation);
        if len(self.spatial_discretisation) != length:
            raise AlgorithmError("The length of spatial solution doesnot match the grid")

        self.temporal_discretisation = self.listvar(temporal_discretisation);
        if len(self.temporal_discretisation) != length:
            raise AlgorithmError("The length of temporal solution doesnot match the grid")

        self.boundary = self.listvar(boundary);
        if len(self.boundary) != length:
            raise AlgorithmError("The length of boundary doesnot match the grid")

        self.initial_conditions = self.listvar(initial_conditions);
        if len(self.initial_conditions) != length:
            raise AlgorithmError("The length of initial_conditions doesnot match the grid")

        self.IO = self.listvar(IO);
        if len(self.IO) != length:
            raise AlgorithmError("The length of IO doesnot match the grid")

        return
    def listvar(self, var):
        '''
        helper function for converting non list objects into list
        '''
        if isinstance(var, list):
            return var
        else:
            return [var]

    def update_definitions(self, computation):
        """ Update the grid based arrays and constants to be declared. """
        
        arrays = set([inp for inp in computation.inputs.keys() if inp.is_grid] + \
            [inp for inp in computation.outputs.keys() if inp.is_grid ] + \
                [inp for inp in computation.inputoutput.keys() if inp.is_grid])
        constant_arrays = set([inp for inp in computation.inputs.keys() if not inp.is_grid] + \
            [inp for inp in computation.outputs.keys() if not inp.is_grid ] + \
                [inp for inp in computation.inputoutput.keys() if not inp.is_grid])
        constants = set(computation.constants)

        self.grid_based_arrays = self.grid_based_arrays.union(arrays)
        # FIXME: Need to think more about constants.
        self.constants = self.constants.union(constant_arrays).union(constants)
        return
        
    def get_OPS_ACC_number(self, computation):
        """ Helper function for writing OPS kernels, which obtains all the of the OPS_ACCs.
        
        :arg computation: The computational kernel to write.
        :returns: A dictionary of OPS_ACC's. """
        ops_accs = {}
        allidbs = list(computation.inputs.keys()) +  list(computation.outputs.keys()) + list(computation.inputoutput.keys())
        grid_based = [al for al in allidbs if al.is_grid]
        # All grid-based OPS_ACCs
        for no,inp in enumerate(grid_based):
            ops_accs[inp] = 'OPS_ACC%d'%no
        # Non grid-based stuff
        nongrid = set(allidbs).difference(set(grid_based))
        for no,inp in enumerate(nongrid):
            ops_accs[inp] = None
        return ops_accs
        
    def array(self, dtype, name, values):
        """ Declare inline arrays in OPSC/C
        
        :arg dtype: The data type of the array.
        :arg name: The name of the array.
        :arg size: The size of the array.
        :arg vals: The list of values.
        :returns: The 
        :rtype: str
        """
        return '%s %s[] = {%s}%s' % (dtype, name, ', '.join([str(s) for s in values]), self.end_of_statement)
             
class AlgorithmError(Exception):

    """ An Exception that occurs  """

    pass

