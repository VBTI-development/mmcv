# Wheel builder images

This directory contains Docker images used to build release wheels.

```text
docker/build/
|-- common/          # Shared wheel-builder entrypoint
|-- jetpack/         # Jetson/JetPack aarch64 wheel builders used by publish.yml
|-- manylinux-cpu/   # x86_64 PyPA manylinux CPU builder image definitions
|-- manylinux-cuda/  # x86_64 PyPA manylinux + CUDA builder image definitions
`-- legacy/          # Older single-image builders kept for manual/reference use
```

## JetPack builders

JetPack builders are pre-built by `.github/workflows/build-wheel-builder-images.yml` and consumed by `publish.yml` via digest-pinned GHCR references, for example:

```text
ghcr.io/vbti-development/onedl-mmcv-builders/jetpack61-torch2110@sha256:<digest>
```

## manylinux CPU and CUDA builders

The wheel builder images are produced by the same workflow from the matrix in `ci/build-matrix.json`:

```bash
python scripts/release_matrix.py gen-builder-matrix
```

Use `workflow_dispatch` with `image_id` for a single-image canary, for example `ml228-cpu`, `ml234-cu128`, or `jetpack61-torch2110`.

## Bootstrap a project-owned registry

To seed the VBTI-development GHCR namespace before changing the digest-pinned
references in `ci/build-matrix.json`, authenticate Docker with a package-write
token and run:

```bash
docker login ghcr.io
bash scripts/bootstrap-wheel-builder-images.sh
```

The script pushes every builder and prints its immutable `image@sha256:...`
reference. Use those references when updating the matrix. Docker repository
names must be lowercase, so the namespace is
`ghcr.io/vbti-development/onedl-mmcv-builders`.

The JetPack builder targets `linux/arm64`. On Arch Linux x86_64, install the
distro-provided QEMU binfmt integration, then use a container-driver Buildx
builder:

```bash
sudo pacman -S qemu-user-static-binfmt
sudo systemctl restart systemd-binfmt
docker buildx create --name onedl-builders --driver docker-container --use
docker buildx inspect --bootstrap
```
