#!/usr/bin/env python3
from __future__ import annotations
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

SAFE_PREFIX_RE = re.compile(r'^[A-Za-z0-9._/-]+$')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename(filename: str) -> str:
    if Path(filename).name != filename:
        raise SystemExit(f'Unsafe wheel filename in manifest: {filename!r}')
    return filename


def safe_prefix(prefix: str) -> str:
    clean = prefix.strip('/')
    if not clean or not SAFE_PREFIX_RE.fullmatch(clean) or '..' in clean.split(
            '/'):
        raise SystemExit(f'Unsafe publish prefix in manifest: {prefix!r}')
    return clean


def validate_record(source: Path, wheel: dict) -> None:
    expected_size = wheel.get('size')
    if expected_size is not None and source.stat().st_size != expected_size:
        raise SystemExit(
            f'{source}: size mismatch; expected {expected_size}, got {source.stat().st_size}'  # noqa: E501
        )
    expected_sha = wheel.get('sha256')
    if expected_sha and sha256(source) != expected_sha:
        raise SystemExit(f'{source}: sha256 mismatch')


def publish_wheel(wheel: dict, artifact_dir: Path, bucket: str,
                  endpoint_url: str, dry_run: bool) -> None:
    filename = safe_filename(wheel['filename'])
    prefix = safe_prefix(wheel['publish_prefix'])
    source = artifact_dir / filename
    if not source.exists():
        raise SystemExit(f'Wheel listed in manifest does not exist: {source}')
    validate_record(source, wheel)
    destination = f's3://{bucket}/{prefix}/{source.name}'
    command = [
        'aws',
        's3',
        'cp',
        str(source),
        destination,
        '--endpoint-url',
        endpoint_url,
        '--only-show-errors',
    ]
    print(f'+ aws s3 cp {source.name} s3://{bucket}/{prefix}/{source.name}')
    if not dry_run:
        subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--artifact-root', type=Path, required=True)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--endpoint-url', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    manifests = sorted(args.artifact_root.glob('*/manifest.json'))
    if not manifests:
        raise SystemExit(
            f'No manifest.json files found under {args.artifact_root}')

    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text())
        for wheel in manifest.get('wheels', []):
            publish_wheel(
                wheel,
                artifact_dir=manifest_path.parent,
                bucket=args.bucket,
                endpoint_url=args.endpoint_url,
                dry_run=args.dry_run,
            )
    return 0


if __name__ == '__main__':
    sys.exit(main())
