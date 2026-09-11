"""Writing a ROM's assets to disk: a model as glTF, its textures as PNG.

Qt-free, so the command line (:mod:`dbkai.cli`) and the viewer's *Extract
Everything* write the same files the same way.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

from dbkai.export.gltf import export_clips, export_glb
from dbkai.export.png import encode_png
from dbkai.game import Asset, AssetKind, GameData
from dbkai.model import scene
from dbkai.model.animation import BoundMotion, Clip, Motion

#: The kinds an extraction writes files for: a model (glTF and its textures)
#: and a texture set. A motion is written only as an animation of a model.
EXTRACTABLE = (AssetKind.MODEL, AssetKind.TEXTURES)


def texture_pngs(
    textures: Iterable[scene.TextureData],
) -> Iterator[tuple[str, bytes]]:
    """Every texture that carries pixels, once per palette, as ``(file name,
    PNG)``: ``<texture>.png``, or ``<texture>_p<n>.png`` when it has several
    palettes."""
    for t in textures:
        if not t.available:
            continue
        for p in range(t.palette_count):
            rgba = t.rgba(p)
            suffix = f"_p{p}" if t.palette_count > 1 else ""
            png = encode_png(rgba.width, rgba.height, rgba.pixels)
            yield f"{Path(t.name).stem}{suffix}.png", png


def bind_clips(
    model: scene.Model,
    motions: Iterable[Motion],
    *,
    matched_only: bool = False,
    clip_filter: str | None = None,
) -> list[tuple[BoundMotion, Clip]]:
    """Each motion bound to ``model``'s skeleton, paired with its clips.

    ``clip_filter`` keeps only the clips whose name contains it, in any case;
    ``matched_only`` drops a motion that drives none of the model's bones.
    """
    needle = (clip_filter or "").lower()
    out: list[tuple[BoundMotion, Clip]] = []
    for motion in motions:
        bound = BoundMotion.bind(model.skeleton, motion)
        if matched_only and not bound.matched:
            continue
        out += [(bound, c) for c in motion.clips if needle in c.name.lower()]
    return out


def export_asset(
    game: GameData,
    asset: Asset,
    out_dir: Path,
    with_motion: bool,
    per_clip: bool = False,
) -> Path | None:
    """Write ``asset`` under ``out_dir``, mirroring its directory in the ROM.

    Its own textures become ``<asset>_<texture>.png``; a model also becomes
    ``<asset>.glb`` with every mesh, and ``with_motion`` animates it with
    every clip of :meth:`GameData.motions_for` that drives its bones. With
    ``per_clip`` the ``.glb`` stays still and each clip gets its own
    ``<asset>__<clip>.glb`` beside it. Returns the model's ``.glb``, or
    ``None`` when the file holds no meshes.
    """
    file = game.load_dse(asset)
    # Built from this file alone: an ``nt_`` model's borrowed textures belong
    # to its textured sibling, which writes them itself.
    own = scene.build(file, asset.name)
    stem = Path(asset.name).stem
    target = out_dir / asset.directory.lstrip("/")
    target.mkdir(parents=True, exist_ok=True)
    for name, png in texture_pngs(own.textures):
        (target / f"{stem}_{name}").write_bytes(png)
    if not own.meshes:
        return None
    model = game.load_model(asset) if asset.is_untextured_variant else own
    motions: list[tuple[BoundMotion, Clip]] = []
    if with_motion:
        sources = (game.load_motion(a) for a in game.motions_for(asset))
        motions = bind_clips(model, sources, matched_only=True)
    path = target / f"{stem}.glb"
    if per_clip:
        path.write_bytes(export_glb(model, None, []))
        export_clips(model, None, motions, target, stem)
    else:
        path.write_bytes(export_glb(model, None, motions))
    return path
