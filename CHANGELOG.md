# Changelog

## [1.0.1](https://github.com/raykrueger/llama-metrics-proxy/compare/v1.0.0...v1.0.1) (2026-04-29)


### Bug Fixes

* set Model template variable to single-select for per-model view ([0dcb7b5](https://github.com/raykrueger/llama-metrics-proxy/commit/0dcb7b5a8413d110173a986e8d2e6aa91a33f3df))
* trigger build and release on release created event ([4091894](https://github.com/raykrueger/llama-metrics-proxy/commit/40918949c8c932f88fac827a90e74b6b349a9b6b))

## 1.0.0 (2026-04-29)


### Bug Fixes

* move Grafana dashboard to grafana/ subdirectory ([a33ae2e](https://github.com/raykrueger/llama-metrics-proxy/commit/a33ae2efbca01fc7d20585868860d1a17ada3c5f))

## 0.1.0 (Initial Release)

* Prometheus metrics aggregator sidecar for llama.cpp router mode
* Discovers models via `/v1/models`
* Scrapes only loaded models to avoid waking sleeping instances
* Emits zero-value metrics for unloaded models to keep time series continuous
* Injects `model` label on every metric for per-model filtering
* Dynamic schema discovery from live scrapes with hardcoded fallback
* Grafana dashboard included
