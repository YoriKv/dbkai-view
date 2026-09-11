"""The headless side as a command line: list a ROM's assets, extract them,
export models to glTF.

    python -m dbkai.cli list ROM [--kind KIND] [--match TEXT]
    python -m dbkai.cli extract ROM OUT [--kind model|textures] [--match TEXT]
        [--motion] [--per-clip]
    python -m dbkai.cli export ROM ASSET OUT.glb [--motion ASSET] [--clip NAME]
        [--all-parts] [--per-clip]

With ``--per-clip`` every clip becomes its own ``<model>__<clip>.glb`` next to
a clip-free ``<model>.glb``; for ``export`` the destination is then a folder.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dbkai.export.gltf import export_clips, export_glb
from dbkai.extract import EXTRACTABLE, bind_clips, export_asset
from dbkai.game import Asset, AssetKind, GameData
from dbkai.nds.rom import RomError

#: The program name in usage and error messages. ``dbkai`` itself is the viewer.
PROG = "python -m dbkai.cli"


def _select(game: GameData, kind: str | None, match: str | None) -> list[Asset]:
    out = []
    for a in game.assets:
        if kind and a.kind.value != kind:
            continue
        if match and match.lower() not in a.path.lower():
            continue
        out.append(a)
    return out


def cmd_list(args: argparse.Namespace) -> int:
    game = GameData.open(args.rom)
    for a in _select(game, args.kind, args.match):
        packed = f" ({a.packed_size} packed)" if a.packed_size else ""
        print(f"{a.kind.value:10s} {a.size:9d}{packed:>16s}  {a.path}")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    game = GameData.open(args.rom)
    out = Path(args.out)
    assets = [a for a in _select(game, args.kind, args.match) if a.kind in EXTRACTABLE]
    failed = 0
    for a in assets:
        try:
            path = export_asset(game, a, out, args.motion, args.per_clip)
            print(f"{a.path} -> {path if path else 'textures only'}")
        except Exception as exc:  # noqa: BLE001 - keep going, report at the end
            failed += 1
            print(f"{a.path}: {exc}", file=sys.stderr)
    print(f"{len(assets) - failed} of {len(assets)} exported", file=sys.stderr)
    return 1 if failed else 0


def cmd_export(args: argparse.Namespace) -> int:
    game = GameData.open(args.rom)
    asset = game.find(args.asset)
    if asset is None:
        print(f"no such asset: {args.asset}", file=sys.stderr)
        return 2
    if args.motion:
        motion = game.find(args.motion)
        if motion is None:
            print(f"no such motion: {args.motion}", file=sys.stderr)
            return 2
        sources = [motion]
    else:
        sources = game.motions_for(asset)
    model = game.load_model(asset)
    if not model.meshes:
        print(f"{args.asset} holds no meshes", file=sys.stderr)
        return 2
    motions = bind_clips(
        model, (game.load_motion(a) for a in sources), clip_filter=args.clip
    )
    visible = None
    if not args.all_parts:
        groups, parts = model.rest_visibility(game.rest_mask(asset))
        visible = model.visible_meshes(groups, parts)
    count = len(visible if visible is not None else model.meshes)
    if args.per_clip:
        stem = Path(asset.name).stem
        folder = Path(args.out)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{stem}.glb").write_bytes(export_glb(model, visible, []))
        written = export_clips(model, visible, motions, folder, stem)
        print(f"{folder}: {count} meshes, {len(written)} clip files")
        return 0
    Path(args.out).write_bytes(export_glb(model, visible, motions))
    print(f"{args.out}: {count} meshes, {len(motions)} clips")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="list the assets in a ROM")
    p.add_argument("rom")
    p.add_argument("--kind", choices=[k.value for k in AssetKind])
    p.add_argument("--match", help="only paths containing this text")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("extract", help="export models as glTF and textures as PNG")
    p.add_argument("rom")
    p.add_argument("out")
    p.add_argument("--kind", choices=[k.value for k in EXTRACTABLE])
    p.add_argument("--match", help="only paths containing this text")
    p.add_argument(
        "--motion",
        action="store_true",
        help="include the matching motions as animations",
    )
    p.add_argument(
        "--per-clip",
        action="store_true",
        help="with --motion, one glTF per clip instead of one file with them all",
    )
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("export", help="export one model as glTF")
    p.add_argument("rom")
    p.add_argument("asset", help="asset path, as printed by list")
    p.add_argument("out")
    p.add_argument("--motion", help="a motion asset to bind (default: the model's own)")
    p.add_argument("--clip", help="only clips whose name contains this text")
    p.add_argument(
        "--all-parts", action="store_true", help="every mesh, not the default selection"
    )
    p.add_argument(
        "--per-clip",
        action="store_true",
        help="OUT is a folder; write one glTF per clip plus one without animation",
    )
    p.set_defaults(func=cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, RomError) as exc:
        # A ROM that is missing or is not one, or a destination that cannot be
        # written: the user's to fix, so a message rather than a traceback.
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
