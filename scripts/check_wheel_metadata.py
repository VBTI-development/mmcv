#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from email.parser import Parser
from pathlib import Path
from zipfile import ZipFile

from release_matrix import load_matrix, resolve_group

DEFAULT_MATRIX = Path('ci/build-matrix.json')
WHEEL_RE = re.compile(
    r'^(?P<name>.+)-(?P<version>[^-]+)-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$'  # noqa: E501
)


def normalize_name(name: str) -> str:
    return name.replace('-', '_').lower()


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


def build_identifier(parts: dict[str, str]) -> str:
    platform = parts['plat']
    if platform.startswith('manylinux'):
        return f"{parts['py']}-manylinux_{_platform_arch(platform)}"
    return f"{parts['py']}-{platform}"


def read_metadata(path: Path) -> dict[str, str]:
    with ZipFile(path) as wheel:
        metadata_names = [
            name for name in wheel.namelist()
            if name.endswith('.dist-info/METADATA')
        ]
        if len(metadata_names) != 1:
            raise SystemExit(
                f'{path.name}: expected one METADATA file, found {len(metadata_names)}'  # noqa: E501
            )
        message = Parser().parsestr(
            wheel.read(metadata_names[0]).decode('utf-8', errors='replace'))
    return {
        'name': message.get('Name', ''),
        'version': message.get('Version', '')
    }


def has_native_ext(path: Path) -> bool:
    with ZipFile(path) as wheel:
        return any(
            name.startswith('mmcv/_ext') and (
                name.endswith('.so') or name.endswith('.pyd'))
            for name in wheel.namelist())


def run_auditwheel_show(path: Path) -> None:
    result = subprocess.run(
        ['auditwheel', 'show', str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(result.stdout)
    if result.returncode != 0:
        raise SystemExit(f'auditwheel show failed for {path.name}')


def validate_wheel(
    path: Path,
    group: dict,
    package_name: str,
    version: str,
    require_ext: bool,
    auditwheel: bool,
) -> dict[str, str]:
    parts = parse_wheel_name(path)
    metadata = read_metadata(path)

    if normalize_name(metadata['name']) != normalize_name(package_name):
        raise SystemExit(
            f"{path.name}: metadata name {metadata['name']!r} != {package_name!r}"  # noqa: E501
        )
    if metadata['version'] != version:
        raise SystemExit(
            f"{path.name}: metadata version {metadata['version']!r} != {version!r}"  # noqa: E501
        )
    if parts['version'] != version:
        raise SystemExit(
            f"{path.name}: filename version {parts['version']!r} != {version!r}"  # noqa: E501
        )

    identifier = build_identifier(parts)
    torch_by_build = group['torch_by_build']
    if identifier not in torch_by_build:
        raise SystemExit(
            f"{path.name}: {identifier!r} is not configured for {group['id']}")
    if require_ext and not has_native_ext(path):
        raise SystemExit(f'{path.name}: missing mmcv._ext native extension')
    if auditwheel:
        run_auditwheel_show(path)

    return {
        'filename': path.name,
        'group': group['id'],
        'cuda': group['cuda'],
        'torch': torch_by_build[identifier]['torch'],
        'build_identifier': identifier,
        'python_tag': parts['py'],
        'platform_tag': parts['plat'],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--wheel-dir', type=Path, required=True)
    parser.add_argument('--matrix', type=Path, default=DEFAULT_MATRIX)
    parser.add_argument('--group-id', required=True)
    parser.add_argument('--package-name', default='onedl-mmcv')
    parser.add_argument('--version', required=True)
    parser.add_argument('--require-ext', action='store_true')
    parser.add_argument('--auditwheel', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()

    group = resolve_group(load_matrix(args.matrix), args.group_id)
    wheels = sorted(args.wheel_dir.glob('*.whl'))
    if not wheels:
        raise SystemExit(f'No wheels found in {args.wheel_dir}')

    records = [
        validate_wheel(
            wheel,
            group=group,
            package_name=args.package_name,
            version=args.version,
            require_ext=args.require_ext,
            auditwheel=args.auditwheel,
        ) for wheel in wheels
    ]

    expected = set(group['torch_by_build'])
    actual = {record['build_identifier'] for record in records}
    missing = expected - actual
    if missing:
        raise SystemExit(
            f"Missing wheels for build identifiers: {', '.join(sorted(missing))}"  # noqa: E501
        )

    payload = {'group': group['id'], 'wheels': records}
    if args.output:
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
