#!/usr/bin/env bash
# Build and publish the release wheel builder images to this project's GHCR
# namespace. Run `docker login ghcr.io` first with a token that can write
# packages in the VBTI-development organisation.
set -euo pipefail

readonly namespace='ghcr.io/vbti-development/onedl-mmcv-builders'
readonly source_repository='https://github.com/vbti-development/onedl-mmcv'
revision="$(git rev-parse --verify HEAD)"
readonly revision

if ! docker buildx version >/dev/null 2>&1; then
  echo 'docker buildx is required' >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo 'Docker is not running or is not accessible' >&2
  exit 1
fi

builder_platforms="$(docker buildx inspect --bootstrap)"
if ! grep -qE 'Platforms:.*linux/arm64' <<<"${builder_platforms}"; then
  cat >&2 <<'EOF'
The selected Buildx builder cannot build linux/arm64, which is required for
jetpack61-torch2110. On Arch Linux x86_64, install the QEMU binfmt integration
and create a container-driver builder, then run this script again:

  sudo pacman -S qemu-user-static-binfmt
  sudo systemctl restart systemd-binfmt
  docker buildx create --name onedl-builders --driver docker-container --use
  docker buildx inspect --bootstrap

Alternatively, build the JetPack image on a native ARM64 machine or GitHub's
ubuntu-24.04-arm runner.
EOF
  exit 1
fi

echo "Publishing builder images to ${namespace}"
echo 'Make sure you have authenticated first: docker login ghcr.io'

while IFS=$'\x1f' read -r id platforms dockerfile base_image auditwheel_plat \
  cuda_pkg_version gcc_toolset spec_tag; do
  image="${namespace}/${id}"

  echo "Building ${image}:${spec_tag} for ${platforms}"
  docker buildx build \
    --platform "${platforms}" \
    --file "${dockerfile}" \
    --tag "${image}:${spec_tag}" \
    --label "org.opencontainers.image.source=${source_repository}" \
    --label "org.opencontainers.image.revision=${revision}" \
    --label "org.opencontainers.image.base.name=${base_image}" \
    --label "org.opencontainers.image.version=${spec_tag}" \
    --build-arg "BASE_IMAGE=${base_image}" \
    --build-arg "CUDA_PKG_VERSION=${cuda_pkg_version}" \
    --build-arg "AUDITWHEEL_PLAT=${auditwheel_plat}" \
    --build-arg "GCC_TOOLSET=${gcc_toolset}" \
    --build-arg 'RPM_ARCH=x86_64' \
    --push \
    .

  digest="$(docker buildx imagetools inspect --format '{{.Manifest.Digest}}' "${image}:${spec_tag}")"
  if [[ -z "${digest}" ]]; then
    echo "Could not determine digest for ${image}:${spec_tag}" >&2
    exit 1
  fi
  printf '%s@%s\n' "${image}" "${digest}"
done < <(
  python scripts/release_matrix.py gen-builder-matrix \
    | python -c '
import json
import sys

for spec in json.load(sys.stdin)["include"]:
    print("\x1f".join((
        spec["id"], spec["platforms"], spec["dockerfile"],
        spec["base_image"], spec["auditwheel_plat"],
        spec["cuda_pkg_version"], spec["gcc_toolset"], spec["spec_tag"],
    )))
'
)
