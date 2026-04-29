# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Prometheus metrics aggregator sidecar for llama.cpp router mode. When llama.cpp runs with `--models-preset`, the `/metrics` endpoint requires a `?model=` parameter and wakes sleeping models on scrape. This proxy discovers models via `/v1/models`, scrapes only loaded ones, and emits zero-value metrics for unloaded models.

The entire proxy logic is in `scraper.py`. There are no tests.

## Development workflow

**Local run:**
```sh
python3 scraper.py --url http://<llama-server>:8080 --port 9091
curl http://localhost:9091/metrics
```

**Build and test via Docker:**
```sh
docker build -t llama-metrics-proxy:test .
docker run --rm -p 9091:9091 llama-metrics-proxy:test --url http://<llama-server> --port 9091
curl http://localhost:9091/metrics
```

Note: on macOS Docker Desktop, containers cannot reach LAN hosts directly. Rsync to a Linux host and build/run there, or use the Python script locally.

## Releases

Both CI workflows trigger on any tag push. Tag format is semver (`vX.Y.Z`):

```sh
git tag vX.Y.Z && git push origin vX.Y.Z
```

- `build.yml` — builds Docker image, pushes to GHCR (`ghcr.io/raykrueger/llama-metrics-proxy`)
- `release.yml` — creates GitHub Release with tarball of `scraper.py`, `grafana-dashboard.json`, `README.md`, `LICENSE`

Update `CHANGELOG.md` before tagging.

## Metrics schema

The proxy learns its metric schema dynamically from live scrapes and updates automatically when llama.cpp adds new metrics. `FALLBACK_METRICS` in `scraper.py` is a hardcoded baseline used only when no loaded model has been scraped yet. Keep it in sync with the metrics llama.cpp actually emits.
