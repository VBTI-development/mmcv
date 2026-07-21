#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys


def gh_api(*args: str) -> str:
    return subprocess.check_output(('gh', 'api', *args), text=True)


def list_caches(repository: str) -> list[dict]:
    output = gh_api('--paginate',
                    f'repos/{repository}/actions/caches?per_page=100')
    caches = []
    for line in output.splitlines():
        if line.strip():
            caches.extend(json.loads(line).get('actions_caches', []))
    return caches


def delete_cache(repository: str, cache_id: int) -> None:
    subprocess.check_call((
        'gh',
        'api',
        '--method',
        'DELETE',
        f'repos/{repository}/actions/caches/{cache_id}',
    ))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--repository', default=os.environ.get('GITHUB_REPOSITORY', ''))
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--current-key', required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.repository:
        raise SystemExit('--repository or GITHUB_REPOSITORY is required')

    old_caches = [
        cache for cache in list_caches(args.repository)
        if cache.get('key', '').startswith(args.prefix)
        and cache.get('key') != args.current_key
    ]
    for cache in old_caches:
        print(f"Deleting old cache {cache['id']}: {cache.get('key')}")
        delete_cache(args.repository, cache['id'])
    print(f'Deleted {len(old_caches)} old cache(s) for prefix {args.prefix!r}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
