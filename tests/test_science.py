import pytest
from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.science import convert, inspect_dump, thermo_blocks, dump_cell


@pytest.mark.parametrize("unit", ["Å³", "angstrom^3", "angstrom**3", "Å³"])
def test_cubic_angstrom_is_valid(unit):
    result = convert(1000, unit, "nanometer**3")
    assert result["value"] == pytest.approx(1)
    assert result["original"]["unit"] == unit


def test_wrong_dimension_is_rejected():
    with pytest.raises(Fault):
        convert(300, "kelvin", "angstrom")


def test_thermo_blocks_do_not_mix_reset_steps(tmp_path):
    path = tmp_path / "log"
    path.write_text("Step Temp Volume TotEng\n0 300 1000 -3\n100 301 1001 -2.9\nLoop time\nStep Temp\n0 600\n100 601\nLoop time\n")
    blocks = thermo_blocks(path)
    assert len(blocks) == 2
    assert blocks[0]["columns"] == ["Step", "Temp", "Volume", "TotEng"]
    assert len(blocks[0]["rows"]) == 2


def test_duplicate_atom_identity_rejected(tmp_path):
    path = tmp_path / "atoms.dump"
    path.write_text("ITEM: TIMESTEP\n0\nITEM: NUMBER OF ATOMS\n2\nITEM: BOX BOUNDS pp pp pp\n0 1\n0 1\n0 1\nITEM: ATOMS id type x y z c_pe\n1 1 0 0 0 -1\n1 1 0.5 0.5 0.5 -2\n")
    with pytest.raises(Fault, match="重复原子身份"):
        inspect_dump(path)


def test_gzip_full_trajectory_cell_and_later_identity_change(tmp_path):
    import gzip
    frame = 'ITEM: TIMESTEP\n{step}\nITEM: NUMBER OF ATOMS\n1\nITEM: BOX BOUNDS pp ff pp\n2 12\n-1 9\n0 10\nITEM: ATOMS id element x y z\n{atom} W 3 0 1\n'
    path = tmp_path / 'trajectory.gz'
    with gzip.open(path, 'wt') as f:
        f.write(frame.format(step=0, atom=1) + frame.format(step=10, atom=2))
    result = inspect_dump(path)
    assert result['frame_count'] == 2 and result['frames'][1]['identity_changed']
    assert result['frames'][0]['cell'] == dict(origin=[2., -1., 0.], vectors=[[10.,0.,0.],[0.,10.,0.],[0.,0.,10.]], volume=1000., boundary=['pp','ff','pp'], periodic=[True,False,True])


def test_restricted_triclinic_recovers_real_lengths():
    result = dump_cell('ITEM: BOX BOUNDS xy xz yz pp pp pp', ['0 15 -2', '-1 10 3', '0 10 1'])
    assert result['origin'] == [2., -1., 0.]
    assert result['vectors'] == [[10.,0.,0.],[-2.,10.,0.],[3.,1.,10.]]
    assert result['volume'] == 1000.


def test_general_triclinic_origin_and_vectors():
    result = dump_cell('ITEM: BOX BOUNDS abc origin pp pp pp', ['0 10 0 2', '-10 0 0 3', '0 0 10 4'])
    assert result['origin'] == [2.,3.,4.] and result['volume'] == 1000.

