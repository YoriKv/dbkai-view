"""The command line, on the synthetic cartridge."""

import pytest

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


def test_usage_names_the_module_not_the_viewer(capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["--help"])
    assert exit_.value.code == 0
    assert capsys.readouterr().out.startswith("usage: python -m dbkai.cli")


def test_a_missing_rom_is_an_error_message(tmp_path, capsys):
    missing = tmp_path / "missing.nds"
    assert main(["list", str(missing)]) == 1
    err = capsys.readouterr().err
    assert "error:" in err and "missing.nds" in err and "Traceback" not in err


def test_extract_writes_models_and_textures_only(rom_bytes, tmp_path, capsys):  # noqa: F811
    rom = tmp_path / "game.nds"
    rom.write_bytes(rom_bytes)
    assert main(["extract", str(rom), str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    # A motion set holds nothing extract writes: it is not reported as done.
    assert "sm_" not in out and "fixture.dse" in out
    with pytest.raises(SystemExit) as exit_:
        main(["extract", str(rom), str(tmp_path / "out"), "--kind", "motion set"])
    assert exit_.value.code == 2


def test_export_refuses_an_asset_without_meshes(rom_bytes, tmp_path, capsys):  # noqa: F811
    rom = tmp_path / "game.nds"
    rom.write_bytes(rom_bytes)
    out = tmp_path / "motion.glb"
    assert main(["export", str(rom), "/sm_100000_NORMAL.dse", str(out)]) == 2
    assert "holds no meshes" in capsys.readouterr().err
    assert not out.exists()
