# Changelog

## 0.1.0 (Initial Release)

* Prometheus metrics aggregator sidecar for llama.cpp router mode
* Discovers models via `/v1/models`
* Scrapes only loaded models to avoid waking sleeping instances
* Emits zero-value metrics for unloaded models to keep time series continuous
* Injects `model` label on every metric for per-model filtering
* Dynamic schema discovery from live scrapes with hardcoded fallback
* Grafana dashboard included
