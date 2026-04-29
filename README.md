# llama-metrics-proxy

A Prometheus metrics aggregator sidecar for [llama.cpp](https://github.com/ggml-org/llama.cpp) router mode.

## The Problem

When llama.cpp runs in router mode (`--models-preset`), the `/metrics` endpoint requires a `?model=` query parameter. Scraping `/metrics` without it returns a 400 error. Worse, scraping an unloaded model wakes it from sleep, defeating the purpose of the router's sleep-on-idle behavior.

## How It Works

On every Prometheus scrape of `/metrics`, this proxy:

1. Calls `/v1/models` to discover all registered models and their load state.
2. Skips models that have entered a failed state entirely — they cannot serve metrics and would trigger repeated failed wakeup attempts on every scrape.
3. For each loaded model, calls `/metrics?model=<id>` and injects a `model` label onto every metric line so results from all models can be distinguished in Prometheus queries.
4. For each unloaded (sleeping) model, emits zero values for all known metrics under that model's label. This keeps time series continuous in Prometheus without waking the model.

The proxy learns its metric schema from the first successful live scrape and updates it automatically whenever a new metric appears in a response. If no model has been scraped yet, a hardcoded fallback schema covering the standard `llamacpp:` metrics is used.

The `model` label value is the model's first configured alias when one is present, otherwise the model ID.

## Requirements

- Python 3.8 or later
- No external dependencies (uses only the standard library)

## Usage

### Running directly

```sh
python3 scraper.py --url http://localhost:8080 --port 9090
```

Prometheus can then scrape `http://<host>:9090/metrics`.

### Running with Docker

Build the image:

```sh
docker build -t llama-metrics-proxy .
```

Run the container, passing CLI flags after the image name:

```sh
docker run -d -p 9090:9090 llama-metrics-proxy --url http://llamacpp:8080 --port 9090
```

The Docker image is based on `python:3.13-slim` and has no additional runtime dependencies.

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--url` | `http://localhost:8080` | Base URL of the llama.cpp server |
| `--port` | `9090` | Port on which to serve `/metrics` |

The proxy exposes only a single endpoint: `GET /metrics`. All other paths return 404.

## Grafana Dashboard

`grafana/grafana-dashboard.json` contains a pre-built Grafana dashboard titled **llama.cpp** (UID `llamacpp-metrics`).

**To import it:**

1. In Grafana, go to **Dashboards > Import**.
2. Click **Upload dashboard JSON file** and select `grafana/grafana-dashboard.json`.
3. Choose your Prometheus datasource from the **Datasource** dropdown.
4. Click **Import**.

The dashboard includes the following panels:

- Active Requests
- Deferred Requests
- Prompt Throughput (stat and time-series)
- Generation Throughput (stat and time-series)
- Prompt Token Rate
- Generation Token Rate
- Active / Deferred Requests (combined time-series)
- Busy Slots per Decode

All panels support a **Model** template variable that lets you filter by one or more models. The variable is populated automatically from the `model` label on the `llamacpp:requests_processing` metric.

The dashboard auto-refreshes every 30 seconds and defaults to a 1-hour time window.

## License

MIT. See [LICENSE](LICENSE).
