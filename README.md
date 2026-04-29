# llama-metrics-proxy

Prometheus metrics aggregator for [llama.cpp](https://github.com/ggml-org/llama.cpp) router mode.

When running llama.cpp with `--models-preset` (router mode), the `/metrics` endpoint requires a `?model=` parameter and will wake sleeping models on scrape. This sidecar solves both problems:

- Discovers models via `/v1/models`
- Only scrapes loaded models (no accidental wakes)
- Emits zero-value metrics for unloaded models so time series stay continuous
- Injects a `model` label on every metric for per-model filtering in Prometheus/Grafana

## Usage

```sh
python3 scraper.py --url http://localhost:8080 --port 9091
```

Prometheus can then scrape `http://<host>:9091/metrics`.

## Docker

```sh
docker build -t llama-metrics-proxy .
docker run -d -p 9091:9091 llama-metrics-proxy --url http://llamacpp:8080 --port 9091
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--url` | `http://localhost:8080` | llama.cpp server base URL |
| `--port` | `9090` | Port to serve `/metrics` on |

## Requirements

Python 3.8+, no external dependencies.

## License

MIT
