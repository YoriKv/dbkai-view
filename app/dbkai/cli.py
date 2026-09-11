"""The headless side as a command line: list a ROM's assets, extract them,
export models to glTF.

    python -m dbkai.cli list ROM [--kind model]
    python -m dbkai.cli extract ROM OUT [--kind model] [--match TEXT] [--motion]
        [--per-clip]
    python -m dbkai.cli export ROM ASSET OUT.glb [--motion ASSET] [--clip NAME]
        [--all-parts] [--per-clip]

With ``--per-clip`` the destination is a folder and every clip becomes its
own ``<model>__<clip>.glb`` next to a clip-free ``<model>.glb``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dbkai.export.gltf import export_clips, export_glb
from dbkai.export.png import encode_png
from dbkai.game import Asset, AssetKind, GameData
from dbkai.model.animation import BoundMotion, Clip


def _open(path: str) -> GameData:
    return GameData.open(path)


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
    game = _open(args.rom)
    for a in _select(game, args.kind, args.match):
        packed = f" ({a.packed_size} packed)" if a.packed_size else ""
        print(f"{a.kind.value:10s} {a.size:9d}{packed:>16s}  {a.path}")
    return 0


def _export_asset(
    game: GameData,
    asset: Asset,
    out_dir: Path,
    with_motion: bool,
    per_clip: bool = False,
) -> Path | None:
    file = game.load_dse(asset)
    stem = Path(asset.name).stem
    target = out_dir / asset.directory.lstrip("/")
    target.mkdir(parents=True, exist_ok=True)
    for t in file.textures:
        if not t.has_data:
            continue
        for p in range(t.palette_count):
            rgba = t.decode(p, not (t.raw_format >> 16) & 1)
            suffix = f"_p{p}" if t.palette_count > 1 else ""
            name = f"{stem}_{Path(t.name).stem}{suffix}.png"
            (target / name).write_bytes(
                encode_png(rgba.width, rgba.height, rgba.pixels)
            )
    if not file.meshes:
        return None
    model = game.load_model(asset)
    motions: list[tuple[BoundMotion, Clip]] = []
    if with_motion:
        for m_asset in game.motions_for(asset):
            motion = game.load_motion(m_asset)
            bound = BoundMotion.bind(model.skeleton, motion)
            if bound.matched:
                motions += [(bound, c) for c in motion.clips]
    path = target / f"{stem}.glb"
    if per_clip:
        path.write_bytes(export_glb(model, None, []))
        export_clips(model, None, motions, target, stem)
        return path
    path.write_bytes(export_glb(model, None, motions))
    return path


#: What extract writes: the kinds that hold models, motions or textures.
_EXTRACTABLE = {
    AssetKind.MODEL,
    AssetKind.TEXTURES,
    AssetKind.MOTION,
    AssetKind.MOTION_SET,
}


def cmd_extract(args: argparse.Namespace) -> int:
    game = _open(args.rom)
    out = Path(args.out)
    assets = [a for a in _select(game, args.kind, args.match) if a.kind in _EXTRACTABLE]
    failed = 0
    for a in assets:
        try:
            path = _export_asset(game, a, out, args.motion, args.per_clip)
            print(f"{a.path} -> {path if path else 'textures only'}")
        except Exception as exc:  # noqa: BLE001 - keep going, report at the end
            failed += 1
            print(f"{a.path}: {exc}", file=sys.stderr)
    print(f"{len(assets) - failed} of {len(assets)} exported", file=sys.stderr)
    return 1 if failed else 0


def cmd_export(args: argparse.Namespace) -> int:
    game = _open(args.rom)
    asset = game.find(args.asset)
    if asset is None:
        print(f"no such asset: {args.asset}", file=sys.stderr)
        return 2
    model = game.load_model(asset)
    motions: list[tuple[BoundMotion, Clip]] = []
    sources = [game.find(args.motion)] if args.motion else game.motions_for(asset)
    for m_asset in sources:
        if m_asset is None:
            print(f"no such motion: {args.motion}", file=sys.stderr)
            return 2
        motion = game.load_motion(m_asset)
        bound = BoundMotion.bind(model.skeleton, motion)
        for c in motion.clips:
            if args.clip and args.clip.lower() not in c.name.lower():
                continue
            motions.append((bound, c))
    visible = None
    if not args.all_parts:
        groups, parts = model.rest_visibility(game.rest_mask())
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dbkai", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="list the assets in a ROM")
    p.add_argument("rom")
    p.add_argument("--kind", choices=[k.value for k in AssetKind])
    p.add_argument("--match", help="only paths containing this text")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("extract", help="export assets as glTF and PNG")
    p.add_argument("rom")
    p.add_argument("out")
    p.add_argument("--kind", choices=[k.value for k in AssetKind])
    p.add_argument("--match")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
