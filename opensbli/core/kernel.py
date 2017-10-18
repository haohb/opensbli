from sympy import flatten, Equality, Indexed
from sympy import Rational, Pow, Integer
from sympy.printing import pprint
from opensbli.core.opensbliobjects import DataSetBase, DataSet, ConstantIndexed, ConstantObject
from opensbli.core.grid import GridVariable, Grididx
from opensbli.core.datatypes import SimulationDataType
from sympy.core.function import _coeff_isneg
from opensbli.utilities.helperfunctions import get_min_max_halo_values
import copy


def dataset_attributes(dset):
    """
    Move to datasetbase? Should we??
    """
    dset.block_number = None
    dset.read_from_hdf5 = False
    dset.dtype = None
    dset.size = None
    dset.halo_ranges = None
    dset.block_name = None
    return dset


def constant_attributes(const):
    const.is_input = True
    const.dtype = None
    const.value = None
    return const


class ConstantsToDeclare(object):
    constants = []

    @staticmethod
    def add_constant(const, value=None, dtype=None):
        if value and const not in ConstantsToDeclare.constants:
            c = constant_attributes(const)
            c.is_input = False
            if dtype:
                c.dtype = dtype
            else:
                c.dtype = SimulationDataType()
            c.value = value
            ConstantsToDeclare.constants += [c]
        elif const not in ConstantsToDeclare.constants:
            c = constant_attributes(const)
            # c.is_input = False
            if dtype:
                c.dtype = dtype
            else:
                c.dtype = SimulationDataType()
            ConstantsToDeclare.constants += [c]
        # print c.__dict__
        return


def copy_block_attributes(block, otherclass):
    """
    Move this to block
    """
    otherclass.block_number = block.blocknumber
    otherclass.ndim = block.ndim
    otherclass.block_name = block.blockname
    return


class StencilObject(object):
    def __init__(self, name, stencil, ndim):
        self.name = name
        self.stencil = stencil
        self.ndim = ndim
        return

    def sort_stencil_indices(self):
        """ Helper function for relative_stencil. Sorts the relative stencil. """
        index_set = self.stencil
        dim = len(list(index_set)[0])
        sorted_index_set = sorted(index_set, key=lambda tup: tuple(tup[i] for i in range(dim)))
        return sorted_index_set


class Kernel(object):

    """ A computational kernel which will be executed over all the grid points and in parallel. """
    mulfactor = {0: 1, 1: 1}
    opsc_access = {'ins': "OPS_READ", "outs": "OPS_WRITE", "inouts": "OPS_RW"}

    def __init__(self, block, computation_name=None):
        """ Set up the computational kernel"""
        copy_block_attributes(block, self)
        self.computation_name = computation_name
        self.kernel_no = block.kernel_counter
        self.kernelname = self.block_name + "Kernel%03d" % self.kernel_no
        block.increase_kernel_counter
        self.equations = []
        self.halo_ranges = [[set(), set()] for d in range(block.ndim)]
        # self.stencil_names = {}
        return

    def set_computation_name(self, name):
        self.computation_name = name
        return

    def _sanitise_kernel(self, type_of_code):
        """Sanitises the kernel equations by updating the datasetbase ranges in the
        DataSetsToDeclare class, finds the Rational constants, updates the constants
        and update the equations
        # TODO
        """
        return

    def __hash__(self):
        h = hash(self._hashable_content())
        self._mhash = h
        return h

    def _hashable_content(self):
        return str(self.kernelname)

    def add_equation(self, equation):
        if isinstance(equation, list):
            self.equations += flatten([equation])
        elif isinstance(equation, Equality):
            self.equations += [equation]
        elif equation:
            pass
        else:
            raise ValueError("Error in kernel add equation.")
        return

    def set_grid_range(self, block):
        self.ranges = copy.deepcopy(block.ranges)
        return

    def set_grid_range_to_zero(self, block):
        self.ranges = []
        for d in range(block.ndim):
            local_range = []
            local_range += [0, 0]
            self.ranges += [local_range]
        return

    def set_halo_range(self, direction, side, types):
        # Rename, halos for a certain kernel
        # if not self.halo_ranges[direction][side]:
            # self.halo_ranges[direction][side] = set([types])
        # else:
        if isinstance(types, set):
            for s in types:
                self.halo_ranges[direction][side].add(s)
        else:
            self.halo_ranges[direction][side].add(types)
        return

    def merge_halo_range(self, halo_range):
        # Required in future for merging 2 kernels
        # Merge the halo ranges for 2 kernels, doesn't check any eqautions
        for direction in range(len(self.halo_ranges)):
            self.halo_ranges[direction][0] = self.halo_ranges[direction][0] | halo_range[direction][0]
            self.halo_ranges[direction][1] = self.halo_ranges[direction][1] | halo_range[direction][1]

        return

    def check_and_merge_kernels(self, kernel):
        """
        We donot check the equations only halo range is checked and updated
        """
        return

    @property
    def required_data_sets(self):
        requires = []
        for eq in self.equations:
            if isinstance(eq, Equality):
                requires += list(eq.rhs.atoms(DataSetBase))
        return requires

    @property
    def lhs_datasets(self):
        datasets = set()
        for eq in self.equations:
            if isinstance(eq, Equality):
                datasets = datasets.union(eq.lhs.atoms(DataSetBase))
        return datasets

    @property
    def rhs_datasets(self):
        datasets = set()
        for eq in self.equations:
            if isinstance(eq, Equality):
                datasets = datasets.union(eq.rhs.atoms(DataSetBase))
        return datasets

    @property
    def Rational_constants(self):
        rcs = set()
        for eq in self.equations:
            if isinstance(eq, Equality):
                rcs = rcs.union(eq.atoms(Rational))
        out = set()
        # Integers are also being returned as Rational numbers, remove any integers
        for rc in rcs:
            if not isinstance(rc, Integer):
                out.add(rc)
        return out

    @property
    def Inverse_constants(self):
        # Only negative powers i.e. they correspond to division and they are stored into constant arrays
        inverse_terms = set()
        for eq in self.equations:
            if isinstance(eq, Equality):
                for at in eq.atoms(Pow):
                    if _coeff_isneg(at.exp) and not (at.base.atoms(Indexed) or isinstance(at, GridVariable)):
                        inverse_terms.add(at)
        return inverse_terms

    @property
    def constants(self):
        consts = set()
        for eq in self.equations:
            if isinstance(eq, Equality):
                consts = consts.union(eq.atoms(ConstantObject))
        return consts

    @property
    def IndexedConstants(self):
        consts = set()
        for eq in self.equations:
            if isinstance(eq, Equality):
                consts = consts.union(eq.atoms(ConstantIndexed))
        return consts

    @property
    def grid_indices_used(self):
        for eq in self.equations:
            if eq.atoms(Grididx):
                return True
        return False

    def get_stencils(self):
        """ Returns the stencils for the datasets used in the kernel
        """
        stencil_dictionary = {}
        datasets = set()
        for eq in self.equations:
            if isinstance(eq, Equality):
                datasets = datasets.union(eq.atoms(DataSet))
        for s in datasets:
            if s.base in stencil_dictionary.keys():
                stencil_dictionary[s.base].add(tuple(s.indices))
            else:
                stencil_dictionary[s.base] = set()
                stencil_dictionary[s.base].add(tuple(s.indices))
        for key, val in stencil_dictionary.iteritems():
            stencil_dictionary[key] = frozenset(val)
        return stencil_dictionary

    def write_latex(self, latex):
        latex.write_string('The kernel is %s' % self.computation_name)
        halo_m, halo_p = get_min_max_halo_values(self.halo_ranges)
        range_of_eval = [[0, 0] for r in range(self.ndim)]
        # print self.computation_name, self.halo_ranges
        # print halo_m, halo_p
        # print range_of_eval
        for d in range(self.ndim):
            range_of_eval[d][0] = self.ranges[d][0] + halo_m[d]
            range_of_eval[d][1] = self.ranges[d][1] + halo_p[d]
        latex.write_string('The ranges are %s' % (','.join([str(d) for d in flatten(range_of_eval)])))
        # latex.write_string('. The range of evaluation is  %s \\ \n\n the halo ranges are %s'%(self.ranges, self.halo_ranges))
        for index, eq in enumerate(self.equations):
            if isinstance(eq, Equality):
                latex.write_expression(eq)
        return

    @property
    def opsc_code(self):
        block_name = self.block_name
        name = self.kernelname
        ins = self.rhs_datasets
        outs = self.lhs_datasets
        inouts = ins.intersection(outs)
        ins = ins.difference(inouts)
        outs = outs.difference(inouts)
        halo_m, halo_p = get_min_max_halo_values(self.halo_ranges)
        range_of_eval = [[0, 0] for r in range(self.ndim)]
        # print self.computation_name, self.ranges, self.halo_ranges
        for d in range(self.ndim):
            range_of_eval[d][0] = self.ranges[d][0] + halo_m[d]
            range_of_eval[d][1] = self.ranges[d][1] + halo_p[d]
        dtype = 'int'
        iter_name = "iteration_range_%d" % (self.kernel_no)
        iter_name_code = ['%s %s[] = {%s};' % (dtype, iter_name, ', '.join([str(s) for s in flatten(range_of_eval)]))]
        code = []
        # pprint(self.stencil_names)
        code += ['ops_par_loop(%s, \"%s\", %s, %s, %s' % (name, self.computation_name, block_name, self.ndim, iter_name)]
        for i in ins:
            code += ['ops_arg_dat(%s, %d, %s, \"%s\", %s)' % (i, 1, self.stencil_names[i], "double", self.opsc_access['ins'])]  # WARNING dtype
        for o in outs:
            code += ['ops_arg_dat(%s, %d, %s, \"%s\", %s)' % (o, 1, self.stencil_names[o], "double", self.opsc_access['outs'])]  # WARNING dtype
        for io in inouts:
            code += ['ops_arg_dat(%s, %d, %s, \"%s\", %s)' % (io, 1, self.stencil_names[io], "double", self.opsc_access['inouts'])]  # WARNING dtype
        if self.IndexedConstants:
            for c in self.IndexedConstants:
                code += ["ops_arg_gbl(&%s, %d, \"%s\", %s)" % (c, 1, "double", self.opsc_access['ins'])]
        if self.grid_indices_used:
            code += ["ops_arg_idx()"]
        code = [',\n'.join(code) + ');\n\n']  # WARNING dtype
        code = iter_name_code + code
        return code

    def ops_argument_call(self, array, stencil, precision, access_type):
        template = 'ops_arg_dat(%s, %d, %s, \"%s\", %s)'
        return template % (array, 1, stencil, self.dtype, access_type)

    def update_block_datasets(self, block):
        """
        Check the following
        a. existing.block_number is same as kernel
        b. set the range to block shape
        c. Update the halo ranges (similar to how we update the halo ranges of a kernel)

        Apply the datasetbase attributes to the dataset and update the parameters
        dataset_attributes(d)
        1. d.block_numner to kernel block number
        2. d.size = block shape
        3. d.halo_ranges to kernel halo ranges
        """
        self.stencil_names = {}
        dsets = self.lhs_datasets.union(self.rhs_datasets)
        from opensbli.core.block import DataSetsToDeclare
        # New logic for the dataset delcarations across blocks
        for d in dsets:
            if d in DataSetsToDeclare.datasetbases:
                ind = DataSetsToDeclare.datasetbases.index(d)
                d1 = DataSetsToDeclare.datasetbases[ind]
                # for direction in range(len(d1.halo_ranges)):
                # d1.halo_ranges[direction][0] = block.get_all_scheme_halos()
                # d1.halo_ranges[direction][1] = block.get_all_scheme_halos()
            else:
                d = dataset_attributes(d)
                d.size = block.shape
                d.block_number = block.blocknumber
                d.halo_ranges = [[set(), set()] for d1 in range(block.ndim)]
                # [[set(), set()] for d in range(block.ndim)]
                for direction in range(len(d.halo_ranges)):
                    d.halo_ranges[direction][0] = block.get_all_scheme_halos()
                    d.halo_ranges[direction][1] = block.get_all_scheme_halos()
                d.block_name = block.blockname
                DataSetsToDeclare.datasetbases += [d]
            if str(d) in block.block_datasets.keys():
                dset = block.block_datasets[str(d)]
                # for direction in range(len(dset.halo_ranges)):
                # dset.halo_ranges[direction][0] = dset.halo_ranges[direction][0] | block.get_all_scheme_halos()
                # dset.halo_ranges[direction][1] = dset.halo_ranges[direction][1] | block.get_all_scheme_halos()
                block.block_datasets[str(d)] = dset
                if block.blocknumber != dset.block_number:
                    raise ValueError("Block number error")
                if block.shape != dset.size:
                    raise ValueError("Shape error")
            else:
                # Update dataset attributes
                d = dataset_attributes(d)
                d.size = block.shape
                d.block_number = block.blocknumber
                d.halo_ranges = [[set(), set()] for d1 in range(block.ndim)]
                for direction in range(len(d.halo_ranges)):
                    d.halo_ranges[direction][0] = block.get_all_scheme_halos()
                    d.halo_ranges[direction][1] = block.get_all_scheme_halos()
                d.block_name = block.blockname
                # Add dataset to block datasets
                block.block_datasets[str(d)] = d
        # Update rational constant attributes
        # rational_constants = self.Rational_constants.union(self.Inverse_constants)
        stens = self.get_stencils()
        for dset, stencil in stens.iteritems():
            if stencil not in block.block_stencils.keys():
                name = 'stencil_%d_%02d' % (block.blocknumber, len(block.block_stencils.keys()))

                block.block_stencils[stencil] = StencilObject(name, stencil, block.ndim)
            if dset not in self.stencil_names:
                self.stencil_names[dset] = block.block_stencils[stencil].name
            else:
                self.stencil_names[dset].add(block.block_stencils[stencil].name)

        # pprint(block.block_stencils)
        # print "\n"
        # pprint(self.stencil_names)
        # for key, value in self.stencil_names.iteritems():
        #     print key, value, stens[key], block.block_stencils[stens[key]].name
        # pprint([self.stencil_names])
        # pprint(stens)
        return

    def update_stencils(self, block):
        self.stencil_names = {}
        stens = self.get_stencils()
        for dset, stencil in stens.iteritems():
            if stencil not in block.block_stencils.keys():
                name = 'stencil_%d_%02d' % (block.blocknumber, len(block.block_stencils.keys()))

                block.block_stencils[stencil] = StencilObject(name, stencil, block.ndim)
            if dset not in self.stencil_names:
                self.stencil_names[dset] = block.block_stencils[stencil].name
            else:
                self.stencil_names[dset].add(block.block_stencils[stencil].name)
        return
