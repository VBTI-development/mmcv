#!/usr/bin/env python3
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

DEFAULT_MATRIX = Path('ci/build-matrix.json')
GROUP_ID_RE = re.compile(r'^(?P<prefix>.+)-torch(?P<compact>\d+)$')
CUDA_PKG_RE = re.compile(r'^\d+-\d+$')
BUILDER_KINDS = {'manylinux-cpu', 'manylinux-cuda', 'jetpack'}
DIGEST_PINNED_GHCR_IMAGE_RE = re.compile(
    r'^(?P<repository>ghcr[.]io/.+)@sha256:[0-9a-f]{64}$')


def load_matrix(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def compact_torch(version: str) -> str:
    parts = version.split('.')
    if len(parts) < 2:
        raise SystemExit(f'Invalid torch version: {version}')
    patch = parts[2] if len(parts) > 2 else '0'
    return f'{parts[0]}{parts[1]}{patch}'


def _targets(matrix: dict) -> dict:
    return {
        **matrix.get('special_targets', {}),
        **matrix.get('cuda_targets', {})
    }


def _torch_by_build(node: dict, torch: str) -> dict:
    platform = node['platform']
    return {
        f"cp{py.replace('.', '')}-{platform}": {
            'torch': torch,
            'torchvision': ''
        }
        for py in node['torch'][torch]['python']
    }


def resolve_group(matrix: dict, group_id: str) -> dict:
    targets = _targets(matrix)
    node = targets.get(group_id)
    if node is not None:
        versions = list(node['torch'])
        if len(versions) != 1:
            raise SystemExit(
                f"Ambiguous build group {group_id!r}; use '{group_id}-torch<compact>'"  # noqa: E501
            )
        torch = versions[0]
    else:
        match = GROUP_ID_RE.match(group_id)
        if match is None:
            raise SystemExit(f'Unknown build group: {group_id}')
        node = targets.get(match.group('prefix'))
        if node is None:
            raise SystemExit(f'Unknown build group: {group_id}')
        matches = [
            v for v in node['torch']
            if compact_torch(v) == match.group('compact')
        ]
        if not matches:
            raise SystemExit(f'Unknown build group: {group_id}')
        if len(matches) > 1:
            raise SystemExit(f'Ambiguous build group: {group_id}')
        torch = matches[0]
    return {
        'id': group_id,
        'cuda': node['cuda'],
        'torch_backend': node.get('torch_backend', ''),
        'publish_prefix_template': node['publish_prefix_template'],
        'torch_by_build': _torch_by_build(node, torch),
    }


def builder_specs(matrix: dict) -> dict:
    return matrix.get('builder_images', {})


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _builder_hash_files(spec: dict) -> list[str]:
    files = [spec['dockerfile'], spec['smoke_script']]
    kind = spec['kind']
    if kind in {'manylinux-cpu', 'manylinux-cuda'}:
        files.append('docker/build/manylinux-cuda/install_ccache.sh')
    elif kind == 'jetpack':
        files.append('docker/build/common/entrypoint.sh')
    files.extend(spec.get('hash_files', []))
    return sorted(set(files))


def _builder_spec_hash(key: str, spec: dict, repo_root: Path) -> str:
    payload = {
        'id': key,
        'kind': spec.get('kind', ''),
        'runner': spec.get('runner', ''),
        'platforms': spec.get('platforms', ''),
        'dockerfile': spec.get('dockerfile', ''),
        'smoke_script': spec.get('smoke_script', ''),
        'base_image': spec.get('base_image', ''),
        'auditwheel_plat': spec.get('auditwheel_plat', ''),
        'cuda_pkg_version': spec.get('cuda_pkg_version', ''),
        'gcc_toolset': spec.get('gcc_toolset', ''),
        'jetpack': spec.get('jetpack', ''),
        'pytorch': spec.get('pytorch', ''),
    }
    for path in _builder_hash_files(spec):
        payload[f'{path}_sha256'] = _file_sha256(repo_root / path)
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]


def _validate_common_builder_spec(key: str, spec: dict) -> None:
    for field in (
            'kind',
            'runner',
            'platforms',
            'dockerfile',
            'smoke_script',
            'base_image',
            'image',
    ):
        if not spec.get(field):
            raise SystemExit(f'{key}: {field} is required')

    kind = spec['kind']
    if kind not in BUILDER_KINDS:
        raise SystemExit(f'{key}: unknown builder image kind {kind!r}')

    image = spec['image']
    if not image.startswith('ghcr.io/'):
        raise SystemExit(f'{key}: image must be a GHCR image path')
    if image != image.lower():
        raise SystemExit(f'{key}: image must be lowercase for Docker')


def _validate_digest_pinned_manylinux_image(prefix: str, spec: dict,
                                            actual_image: str) -> None:
    match = DIGEST_PINNED_GHCR_IMAGE_RE.fullmatch(actual_image)
    if match is None:
        raise SystemExit(
            f'{prefix}: manylinux_image must be a digest-pinned GHCR image, '
            f'got {actual_image!r}')

    expected_repository = spec['image']
    actual_repository = match.group('repository')
    if actual_repository != expected_repository:
        raise SystemExit(
            f'{prefix}: manylinux_image repository {actual_repository!r} != '
            f'expected {expected_repository!r}')


def _validate_manylinux_cpu_builder_spec(key: str, spec: dict) -> None:
    if not spec.get('auditwheel_plat'):
        raise SystemExit(f'{key}: auditwheel_plat is required')

    base_image = spec['base_image']
    if not base_image.startswith('quay.io/pypa/'):
        raise SystemExit(f'{key}: base_image must be a PyPA manylinux image')
    if 'manylinux_2_28' not in spec['dockerfile']:
        raise SystemExit(
            f'{key}: CPU builder must use a manylinux_2_28 Dockerfile')
    if 'manylinux_2_28' not in base_image:
        raise SystemExit(f'{key}: CPU builder must use a manylinux_2_28 base')
    if spec['auditwheel_plat'] != 'manylinux_2_28_x86_64':
        raise SystemExit(
            f'{key}: auditwheel_plat must be manylinux_2_28_x86_64')


def _validate_manylinux_builder_spec(key: str, spec: dict) -> None:
    for field in ('auditwheel_plat', 'cuda_pkg_version'):
        if not spec.get(field):
            raise SystemExit(f'{key}: {field} is required')

    base_image = spec['base_image']
    if not base_image.startswith('quay.io/pypa/'):
        raise SystemExit(f'{key}: base_image must be a PyPA manylinux image')

    cuda_pkg = spec['cuda_pkg_version']
    if not CUDA_PKG_RE.fullmatch(cuda_pkg):
        raise SystemExit(f"{key}: cuda_pkg_version must look like '12-8'")

    dockerfile = spec['dockerfile']
    auditwheel_plat = spec['auditwheel_plat']
    if 'manylinux2014' in dockerfile:
        if 'manylinux2014' not in base_image:
            raise SystemExit(
                f'{key}: manylinux2014 Dockerfile must use manylinux2014 base'  # noqa: E501
            )
        if auditwheel_plat != 'manylinux2014_x86_64':
            raise SystemExit(
                f'{key}: auditwheel_plat must be manylinux2014_x86_64'  # noqa: E501
            )
    elif 'manylinux_2_34' in dockerfile:
        if 'manylinux_2_34' not in base_image:
            raise SystemExit(
                f'{key}: manylinux_2_34 Dockerfile must use manylinux_2_34 base'  # noqa: E501
            )
        if auditwheel_plat != 'manylinux_2_34_x86_64':
            raise SystemExit(
                f'{key}: auditwheel_plat must be manylinux_2_34_x86_64'  # noqa: E501
            )
    else:
        raise SystemExit(f'{key}: unknown manylinux Dockerfile family')


def _validate_jetpack_builder_spec(key: str, spec: dict) -> None:
    for field in ('jetpack', 'pytorch'):
        if not spec.get(field):
            raise SystemExit(f'{key}: {field} is required')
    if spec['runner'] != 'ubuntu-24.04-arm':
        raise SystemExit(f'{key}: JetPack builders must use arm runners')
    if spec['platforms'] != 'linux/arm64':
        raise SystemExit(f'{key}: JetPack builders must target linux/arm64')
    if 'Dockerfile.jetpack' not in spec['dockerfile']:
        raise SystemExit(
            f'{key}: JetPack builder must use a JetPack Dockerfile')
    if not spec['base_image'].startswith('nvcr.io/nvidia/l4t-jetpack:'):
        raise SystemExit(
            f'{key}: JetPack base_image must be an NVIDIA L4T image')


def validate_builder_specs(matrix: dict) -> None:
    specs = builder_specs(matrix)
    if matrix.get('cuda_targets') and not specs:
        raise SystemExit('builder_images section is required')

    for key, spec in specs.items():
        _validate_common_builder_spec(key, spec)
        kind = spec['kind']
        if kind == 'manylinux-cpu':
            _validate_manylinux_cpu_builder_spec(key, spec)
        elif kind == 'manylinux-cuda':
            _validate_manylinux_builder_spec(key, spec)
        elif kind == 'jetpack':
            _validate_jetpack_builder_spec(key, spec)


def iter_build_units(matrix: dict):
    sections = (matrix.get('special_targets',
                           {}), matrix.get('cuda_targets', {}))
    for targets in sections:
        for prefix, node in targets.items():
            for torch in node['torch']:
                yield prefix, torch, node


def validate_cuda_builder_refs(matrix: dict) -> None:
    specs = builder_specs(matrix)
    for prefix, node in matrix.get('cuda_targets', {}).items():
        key = node.get('builder_image', '')
        if not key:
            raise SystemExit(f'{prefix}: builder_image is required')
        if key not in specs:
            raise SystemExit(f'{prefix}: unknown builder_image {key!r}')

        spec = specs[key]
        if spec['kind'] != 'manylinux-cuda':
            raise SystemExit(f'{prefix}: builder_image must be manylinux-cuda')
        expected_cuda_pkg = node['cuda'].replace('.', '-')
        if spec['cuda_pkg_version'] != expected_cuda_pkg:
            raise SystemExit(
                f"{prefix}: builder image CUDA package {spec['cuda_pkg_version']!r} != {expected_cuda_pkg!r}"  # noqa: E501
            )

        target_toolset = node.get('gcc_toolset', '')
        spec_toolset = spec.get('gcc_toolset', '')
        if spec_toolset != target_toolset:
            raise SystemExit(
                f'{prefix}: builder image gcc_toolset {spec_toolset!r} != target {target_toolset!r}'  # noqa: E501
            )
        actual_image = node.get('manylinux_image', '')
        _validate_digest_pinned_manylinux_image(prefix, spec, actual_image)


def validate_special_builder_refs(matrix: dict) -> None:
    specs = builder_specs(matrix)
    for prefix, node in matrix.get('special_targets', {}).items():
        key = node.get('builder_image', '')
        if not key:
            continue
        if key not in specs:
            raise SystemExit(f'{prefix}: unknown builder_image {key!r}')
        spec = specs[key]
        if node['platform'].endswith('x86_64') and node['cuda'] == 'cpu':
            if spec['kind'] != 'manylinux-cpu':
                raise SystemExit(
                    f'{prefix}: CPU builder_image must be manylinux-cpu')
            actual_image = node.get('manylinux_image', '')
            _validate_digest_pinned_manylinux_image(prefix, spec, actual_image)
            continue
        if node['platform'].endswith('aarch64') and spec['kind'] != 'jetpack':
            raise SystemExit(
                f'{prefix}: aarch64 builder_image must be jetpack')
        versions = list(node['torch'])
        if len(versions) == 1 and spec.get('pytorch', '') != versions[0]:
            raise SystemExit(
                f"{prefix}: builder image PyTorch {spec.get('pytorch', '')!r} != target {versions[0]!r}"  # noqa: E501
            )


def validate(matrix: dict, include_builders: bool = True) -> None:
    compat = matrix.get('pytorch_compatibility', {})
    if not compat:
        raise SystemExit('pytorch_compatibility section is required')
    overlap = set(matrix.get('special_targets', {})) & set(
        matrix.get('cuda_targets', {}))
    if overlap:
        raise SystemExit(
            f"target id used in both sections: {', '.join(sorted(overlap))}")
    for prefix, node in matrix.get('cuda_targets', {}).items():
        if not node.get('manylinux_image'):
            raise SystemExit(
                f'cuda target {prefix!r} is missing manylinux_image')
    if include_builders:
        validate_builder_specs(matrix)
        validate_cuda_builder_refs(matrix)
        validate_special_builder_refs(matrix)
    for prefix, node in _targets(matrix).items():
        compacts = [compact_torch(torch) for torch in node['torch']]
        if len(compacts) != len(set(compacts)):
            raise SystemExit(
                f'{prefix}: torch versions collide on a compact id')
    for prefix, torch, node in iter_build_units(matrix):
        rule = compat.get(torch)
        if rule is None:
            raise SystemExit(
                f'{prefix}: torch {torch} is absent from pytorch_compatibility'
            )
        cuda = node['cuda']
        if cuda != 'cpu' and cuda not in rule['cuda_stable']:
            raise SystemExit(
                f'{prefix}: torch {torch} + CUDA {cuda} is not an official stable combo'  # noqa: E501
            )
        for py in node['torch'][torch]['python']:
            if py not in rule['python']:
                raise SystemExit(
                    f'{prefix}: torch {torch} does not support Python {py}')
        compact = compact_torch(torch)
        expected_prefix = f'{prefix}-torch{compact}/onedl-mmcv/'
        actual_prefix = node['publish_prefix_template'].format(
            torch_compact=compact)
        if actual_prefix != expected_prefix:
            raise SystemExit(
                f'{prefix}: publish prefix {actual_prefix!r} != expected {expected_prefix!r}'  # noqa: E501
            )


def gen_linux_matrix(matrix: dict) -> dict:
    include = []
    for prefix, torch, node in iter_build_units(matrix):
        platform = node['platform']
        if not platform.endswith('x86_64'):
            continue
        cuda = node['cuda']
        cibw_build = ' '.join(f"cp{py.replace('.', '')}-{platform}"
                              for py in node['torch'][torch]['python'])
        name = (f'Linux CPU wheels (torch {torch})' if cuda == 'cpu' else
                f'Linux CUDA {cuda} wheels (torch {torch})')
        gcc_toolset = node.get('gcc_toolset', '')
        cc_bin = (f'/opt/rh/gcc-toolset-{gcc_toolset}/root/usr/bin:'
                  if gcc_toolset else '')
        cc_lib = (f'/opt/rh/gcc-toolset-{gcc_toolset}/root/usr/lib64:'
                  if gcc_toolset else '')
        include.append({
            'id':
            f'{prefix}-torch{compact_torch(torch)}',
            'name':
            name,
            'runner':
            'ubuntu-24.04',
            'force_cuda':
            '0' if cuda == 'cpu' else '1',
            'cuda':
            cuda,
            'manylinux_image':
            node['manylinux_image'],
            'cibw_build':
            cibw_build,
            'torch_cuda_arch_list':
            node.get('torch_cuda_arch_list', ''),
            'gcc_toolset':
            gcc_toolset,
            'cc_path_prefix':
            cc_bin,
            'cc_ld_prefix':
            cc_lib,
        })
    return {'include': include}


def gen_builder_matrix(matrix: dict,
                       image_id: str = '',
                       repo_root: Path = Path('.')) -> dict:
    specs = builder_specs(matrix)
    if image_id:
        if image_id not in specs:
            raise SystemExit(f'unknown builder image: {image_id}')
        items = [(image_id, specs[image_id])]
    else:
        items = sorted(specs.items())

    include = []
    for key, spec in items:
        include.append({
            'id':
            key,
            'kind':
            spec['kind'],
            'runner':
            spec['runner'],
            'platforms':
            spec['platforms'],
            'dockerfile':
            spec['dockerfile'],
            'smoke_script':
            spec['smoke_script'],
            'base_image':
            spec['base_image'],
            'auditwheel_plat':
            spec.get('auditwheel_plat', ''),
            'cuda_pkg_version':
            spec.get('cuda_pkg_version', ''),
            'gcc_toolset':
            spec.get('gcc_toolset', ''),
            'image':
            spec['image'],
            'spec_tag':
            f'spec-{_builder_spec_hash(key, spec, repo_root)}',
        })
    return {'include': include}


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Release build matrix source of truth')
    parser.add_argument('--matrix', type=Path, default=DEFAULT_MATRIX)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser(
        'validate',
        help='check every (cuda, torch, python) against pytorch_compatibility')
    gen = commands.add_parser(
        'gen-matrix', help='emit the cibuildwheel matrix as compact JSON')
    gen.add_argument('--kind', choices=['linux'], default='linux')
    builders = commands.add_parser(
        'gen-builder-matrix', help='emit the wheel builder image matrix')
    builders.add_argument('--image-id', default='')
    wheel_builders = commands.add_parser(
        'gen-wheel-builder-matrix', help='emit the wheel builder image matrix')
    wheel_builders.add_argument('--image-id', default='')
    args = parser.parse_args()

    matrix = load_matrix(args.matrix)
    repo_root = args.matrix.resolve().parent.parent
    if args.command == 'validate':
        validate(matrix)
        print(
            f'ok: {sum(1 for _ in iter_build_units(matrix))} build groups valid'  # noqa: E501
        )
    elif args.command == 'gen-matrix':
        validate(matrix)
        print(json.dumps(gen_linux_matrix(matrix), separators=(',', ':')))
    else:
        validate(matrix)
        print(
            json.dumps(
                gen_builder_matrix(matrix, args.image_id, repo_root),
                separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
