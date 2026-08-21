# Deployment

## CPU deployment with MiniMax

Recommended first production node:

- Ubuntu 22.04 or 24.04
- 8 vCPU
- 16 GB RAM
- 200 GB persistent NVMe storage
- Docker Engine with Compose v2

Copy `.env.example` to `.env`, set `SUB2API_API_KEY` and `MINIMAX_API_KEY`,
keep `PREFER_LOCAL_QWEN_TTS=false`, then run:

```bash
git submodule update --init --recursive
docker compose up --build -d
```

Generated assets and SQLite data persist under `./data`. Back up this directory
before upgrading or moving the service.

The included Compose file is suitable for a private beta. Before exposing it
to the public internet, add a TLS reverse proxy, authentication, rate limits,
media authorization and off-host backups.

## Local Qwen deployment

Local Qwen is optional and is not installed in the default API image. Install
`services/api/requirements-qwen.txt` in a dedicated Python 3.12 environment,
download both VoiceDesign and Base checkpoints, then configure their paths in
`.env`.

The worker automatically selects CUDA, Apple MPS or CPU. NVIDIA deployment
uses BF16 when supported and enables FlashAttention 2 when installed. A 24 GB
GPU is recommended for the 1.7B locked-voice workflow.

For a containerized GPU worker, extend the API image with PyTorch, the optional
Qwen requirements and NVIDIA Container Toolkit. Keep speech concurrency at one
job per GPU until the deployment has been benchmarked.

## Scaling

The first scaling boundary should be the media worker, not the web UI:

1. Move media production into a durable job queue.
2. Keep API and web services stateless.
3. Move SQLite to PostgreSQL.
4. Use a shared filesystem or object storage if workers run on different nodes.
5. Allocate one rendering/TTS slot per worker and apply provider rate limits.
