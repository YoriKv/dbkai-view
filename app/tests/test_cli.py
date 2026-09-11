"""The command line, on the synthetic cartridge."""

from dbkai.cli import main
from tests.test_game import rom_bytes  # noqa: F401 - fixture


def test_export_per_clip_and_extract(rom_bytes, tmp_path, capsys):  # noqa: F811
    rom = tmp_path / "game.nds"
    rom.write_bytes(rom_bytes)
    out = tmp_path / "hero"
    assert (
        main(
            [
                "export",
                str(rom),
                "/archiveDBK.dsa/mdl/chr/101100_hero.dse",
                str(out),
                "--per-clip",
            ]
        )
        == 0
    )
    assert sorted(p.name for p in out.iterdir()) == [
        "101100_hero.glb",
        "101100_hero__000_spin.glb",
    ]
    assert "1 clip files" in capsys.readouterr().out
    everything = tmp_path / "all"
    assert (
        main(
            [
                "extract",
                str(rom),
                str(everything),
                "--match",
                "hero",
                "--motion",
                "--per-clip",
            ]
        )
        == 0
    )
    names = sorted(p.name for p in (everything / "archiveDBK.dsa/mdl/chr").iterdir())
    assert "101100_hero__000_spin.glb" in names and "101100_hero.glb" in names
    assert main(["list", str(rom), "--kind", "model"]) == 0
    assert "101100_hero.dse" in capsys.readouterr().out
