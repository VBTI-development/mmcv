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
