#!/usr/bin/env python3
from __future__ import annotations
import argparse
import os
import re
import sys
import tomllib
from pathlib import Path

SAFE_TAG_RE = re.compile(r'^v?[0-9]+[.][0-9]+[.][0-9]+[A-Za-z0-9_.+-]*$')


def read_project_version(pyproject: Path) -> str:
    with pyproject.open('rb') as handle:
        data = tomllib.load(handle)
    try:
        return data['project']['version']
    except KeyError as exc:
        raise SystemExit(
            f'{pyproject} does not define project.version') from exc


def normalize_tag(tag: str) -> str:
    if tag.startswith('refs/tags/'):
        tag = tag.removeprefix('refs/tags/')
    return tag.removeprefix('v')


def write_github_output(version: str, tag: str) -> None:
    output = os.getenv('GITHUB_OUTPUT')
    if not output:
        raise SystemExit('GITHUB_OUTPUT is not set')
    with open(output, 'a', encoding='utf-8') as handle:
        handle.write(f'version={version}\n')
        handle.write(f'tag={tag}\n')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'tag', nargs='?', default=os.getenv('GITHUB_REF_NAME', ''))
    parser.add_argument(
        'pyproject', nargs='?', type=Path, default=Path('pyproject.toml'))
    parser.add_argument('--write-github-output', action='store_true')
    parser.add_argument('--allow-missing-tag', action='store_true')
    args = parser.parse_args()

    expected = read_project_version(args.pyproject)
    if not args.tag:
        if not args.allow_missing_tag:
            raise SystemExit('release tag is required')
        print(expected)
        if args.write_github_output:
            write_github_output(expected, '')
        return 0

    raw_tag = args.tag.removeprefix('refs/tags/')
    if not SAFE_TAG_RE.fullmatch(raw_tag):
        raise SystemExit(f'unsafe or invalid release tag: {args.tag!r}')

    actual = normalize_tag(args.tag)
    if actual != expected:
        raise SystemExit(
            f'Release tag {args.tag!r} does not match project.version {expected!r}'  # noqa: E501
        )

    print(expected)
    if args.write_github_output:
        write_github_output(expected, args.tag)
    return 0


if __name__ == '__main__':
    sys.exit(main())
