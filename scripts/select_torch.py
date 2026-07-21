#!/usr/bin/env python3
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

from release_matrix import load_matrix, resolve_group

DEFAULT_MATRIX = Path('ci/build-matrix.json')


def current_build_identifier() -> str:
    return f'cp{sys.version_info.major}{sys.version_info.minor}-manylinux_x86_64'  # noqa: E501


def install_args(group: dict, build_identifier: str) -> list[str]:
    try:
        spec = group['torch_by_build'][build_identifier]
    except KeyError as exc:
        valid = ', '.join(sorted(group['torch_by_build']))
        raise SystemExit(
            f"{build_identifier!r} is not configured for {group['id']}; valid: {valid}"  # noqa: E501
        ) from exc

    packages = [f"torch=={spec['torch']}"]
    torchvision = spec.get('torchvision', '')
    packages.append(
        f'torchvision=={torchvision}' if torchvision else 'torchvision')

    args = ['uv', 'pip', 'install', '--python', sys.executable, '--system']
    backend = group.get('torch_backend', '')
    if backend:
        args.append(f'--torch-backend={backend}')
    return [*args, *packages]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--matrix', type=Path, default=DEFAULT_MATRIX)
    parser.add_argument('--group-id', required=True)
    parser.add_argument('--build-identifier', default='')
    args = parser.parse_args()

    group = resolve_group(load_matrix(args.matrix), args.group_id)
    build_identifier = args.build_identifier or current_build_identifier()
    subprocess.run(install_args(group, build_identifier), check=True)
    subprocess.run([
        'uv',
        'pip',
        'install',
        '--python',
        sys.executable,
        '--system',
        '-r',
        'pyproject.toml',
        '--group',
        'optional',
        '--group',
        'build',
    ],
                   check=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
