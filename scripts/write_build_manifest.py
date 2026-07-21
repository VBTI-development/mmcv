#!/usr/bin/env python3
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from zipfile import ZipFile

from release_matrix import compact_torch, load_matrix, resolve_group

DEFAULT_MATRIX = Path('ci/build-matrix.json')
WHEEL_RE = re.compile(
    r'^(?P<name>.+)-(?P<version>[^-]+)-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$'  # noqa: E501
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def parse_wheel_name(path: Path) -> dict[str, str]:
    match = WHEEL_RE.match(path.name)
    if not match:
        raise SystemExit(f'Invalid wheel filename: {path.name}')
    return match.groupdict()


def _platform_arch(platform: str) -> str:
    if 'x86_64' in platform:
        return 'x86_64'
    if 'aarch64' in platform:
        return 'aarch64'
    return platform


def build_identifier(path: Path) -> str:
    parts = parse_wheel_name(path)
    platform = parts['plat']
    if platform.startswith('manylinux'):
        return f"{parts['py']}-manylinux_{_platform_arch(platform)}"
    return f"{parts['py']}-{platform}"


def has_native_ext(path: Path) -> bool:
    with ZipFile(path) as wheel:
        return any(
            name.startswith('mmcv/_ext') and (
                name.endswith('.so') or name.endswith('.pyd'))
            for name in wheel.namelist())


def publish_prefix(group: dict, torch: str) -> str:
    return group['publish_prefix_template'].format(
        torch_compact=compact_torch(torch))


def wheel_record(path: Path, group: dict) -> dict[str, object]:
    identifier = build_identifier(path)
    try:
        spec = group['torch_by_build'][identifier]
    except KeyError as exc:
        raise SystemExit(
            f'{path.name}: no torch mapping for build identifier {identifier}'
        ) from exc
    return {
        'file': str(path),
        'filename': path.name,
        'sha256': sha256(path),
        'size': path.stat().st_size,
        'group': group['id'],
        'cuda': group['cuda'],
        'torch': spec['torch'],
        'torch_backend': group['torch_backend'],
        'build_identifier': identifier,
        'publish_prefix': publish_prefix(group, spec['torch']),
        'native_extension': has_native_ext(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheel-dir', type=Path, required=True)
    parser.add_argument('--matrix', type=Path, default=DEFAULT_MATRIX)
    parser.add_argument('--group-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--git-sha', default=os.getenv('GITHUB_SHA', ''))
    parser.add_argument('--tag', default=os.getenv('GITHUB_REF_NAME', ''))
    args = parser.parse_args()

    group = resolve_group(load_matrix(args.matrix), args.group_id)
    wheels = sorted(args.wheel_dir.glob('*.whl'))
    if not wheels:
        raise SystemExit(f'No wheels found in {args.wheel_dir}')

    manifest = {
        'version': args.version,
        'tag': args.tag,
        'git_sha': args.git_sha,
        'build_group': group['id'],
        'cuda': group['cuda'],
        'torch_backend': group['torch_backend'],
        'wheels': [wheel_record(wheel, group) for wheel in wheels],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
