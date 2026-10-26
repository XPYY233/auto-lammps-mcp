import pytest
from auto_lammps_mcp.core import Fault
from auto_lammps_mcp.science import convert, inspect_dump, thermo_blocks


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

