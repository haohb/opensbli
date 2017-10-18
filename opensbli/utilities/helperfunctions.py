from opensbli.core.opensbliobjects import DataSet, CoordinateObject
import h5py


def get_min_max_halo_values(halos):
    halo_m = []
    halo_p = []
    for direction in range(len(halos)):
        if halos[direction][0]:
            hal = [d.get_halos(0) for d in halos[direction][0]]
            halo_m += [min(hal)]
        else:
            halo_m += [0]
        if halos[direction][1]:
            hal = [d.get_halos(1) for d in halos[direction][1]]
            halo_p += [max(hal)]
        else:
            halo_p += [0]
    return halo_m, halo_p

def increment_dataset(expression, direction, value):
    """ Increments an expression containing datasets by the given increment and direction.
    arg: object: expression: A SymPy expression containing datasets to have their indices updated.
    arg: int: direction: The integer direction to apply the increment. (which DataSet axis to apply to)
    arg: int: value: The positive or negative change to apply to the DataSet's index.
    returns: object: expression: The original expression updated to the new DataSet location.
    """
    for dset in expression.atoms(DataSet):
        loc = list(dset.indices)
        loc[direction] = loc[direction] + value
        new_dset = dset.base[loc]
        expression = expression.replace(dset, new_dset)
    return expression


def dot(v1, v2):
    out = 0
    if isinstance(v1, list):
        if len(v1) == len(v2):
            for i in range(len(v1)):
                out += v1[i]*v2[i]
            return out
        else:
            raise ValueError("")
    else:
        return v1*v2


def decreasing_order(s1, s2):
    return cmp(len(s2.atoms(CoordinateObject)), len(s1.atoms(CoordinateObject)))


def increasing_order(s1, s2):
    return cmp(len(s1.atoms(CoordinateObject)), len(s2.atoms(CoordinateObject)))


def sort_funcitons(fns, increasing_order=True):
    """Sorts the functions based on the number of arguments in
    increasing order
    """
    if increasing_order:
        return (sorted(fns, cmp=increasing_order))
    else:
        return (sorted(fns, cmp=decreasing_order))


def get_inverse_deltas(delta):
    from opensbli.core.codegeneration.opsc import rc
    if delta in rc.existing:
        return rc.existing[delta]
    else:
        name = rc.name
        b, exp = delta.as_base_exp()
        rc.name = "inv_%d"
        inv_delta_name = rc.get_next_rational_constant(delta)
        rc.name = name
        return inv_delta_name

def set_hdf5_metadata(dset, halos, npoints, block):
    """ Function to set hdf5 metadata required by OPS to a dataset. """
    d_m = [halos[0], halos[0]]
    d_p = [halos[1], halos[1]]
    dset.attrs.create("d_p", d_p, dtype="int32")
    dset.attrs.create("d_m", d_m, dtype="int32")
    dset.attrs.create("dim", [1], dtype="int32")
    dset.attrs.create("ops_type", u"ops_dat",dtype="S7")
    dset.attrs.create("block_index", [0], dtype="int32")
    dset.attrs.create("base", [0 for i in range(block.ndim)], dtype="int32")
    dset.attrs.create("type", u"double",dtype="S15")
    dset.attrs.create("block", u"%s" % block.blockname,dtype="S25")
    dset.attrs.create("size", npoints, dtype="int32")
    return

def output_hdf5(array, array_name, halos, npoints, block):
    """ Creates an HDF5 file for reading in data to a simulation, 
    sets the metadata required by the OPS library. """
    with h5py.File('data.h5', 'w') as hf:
        # Set atttributes for group
        g1 = hf.create_group(block.blockname)
        g1.attrs.create("dims", [block.ndim], dtype="int32")
        g1.attrs.create("ops_type", u"ops_block",dtype="S9")
        g1.attrs.create("index", [0], dtype="int32")
        dset = g1.create_dataset('%s_B0' % array_name, data=array)
        set_hdf5_metadata(dset, halos, npoints, block)
    return
