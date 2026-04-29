#!/usr/bin/env python3
"""
Prometheus metrics aggregator for llama.cpp router mode.

llama.cpp's /metrics endpoint requires a ?model= parameter when running in
router mode (--models-preset). Scraping without one returns a 400 error, and
scraping an unloaded model wakes it from sleep. This sidecar works around both
problems by discovering models via /v1/models, scraping only loaded ones, and
emitting zero-value metrics for unloaded models so time series remain continuous.
"""
import argparse
import json
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from functools import partial


def get_models(base_url):
    """Return all non-failed models from /v1/models as (model_id, label, loaded) tuples.

    label is the first alias if available, otherwise the model id.
    loaded is True only when status.value == "loaded" and the model has not failed.
    Failed models are excluded entirely — they cannot serve metrics and would
    trigger repeated failed wakeup attempts on every scrape.
    """
    with urllib.request.urlopen(f"{base_url}/v1/models", timeout=10) as resp:
        data = json.loads(resp.read())
    models = []
    for m in data.get("data", []):
        status = m.get("status", {})
        if status.get("failed"):
            continue
        model_id = m["id"]
        aliases = m.get("aliases", [])
        label = aliases[0] if aliases else model_id
        loaded = status.get("value") == "loaded"
        models.append((model_id, label, loaded))
    return models


# Metric schema discovered from a live scrape: list of (name, type, help_text).
# Updated on every successful scrape so new metrics added by llama.cpp are
# picked up automatically without restarting this proxy.
_metric_schema = []

# Used when _metric_schema is empty (i.e. no loaded model has been scraped yet).
# Matches the metrics emitted by llama.cpp as of the time this was written.
FALLBACK_METRICS = [
    ("llamacpp:prompt_tokens_total",            "counter", "Number of prompt tokens processed."),
    ("llamacpp:prompt_seconds_total",           "counter", "Prompt process time"),
    ("llamacpp:tokens_predicted_total",         "counter", "Number of generation tokens processed."),
    ("llamacpp:tokens_predicted_seconds_total", "counter", "Predict process time"),
    ("llamacpp:n_decode_total",                 "counter", "Total number of llama_decode() calls"),
    ("llamacpp:n_tokens_max",                   "counter", "Largest observed n_tokens."),
    ("llamacpp:n_busy_slots_per_decode",        "counter", "Average number of busy slots per llama_decode() call"),
    ("llamacpp:prompt_tokens_seconds",          "gauge",   "Average prompt throughput in tokens/s."),
    ("llamacpp:predicted_tokens_seconds",       "gauge",   "Average generation throughput in tokens/s."),
    ("llamacpp:requests_processing",            "gauge",   "Number of requests processing."),
    ("llamacpp:requests_deferred",              "gauge",   "Number of requests deferred."),
]


def parse_schema(text):
    """Extract (name, type, help_text) tuples from a Prometheus text payload."""
    help_map = {}
    type_map = {}
    for line in text.splitlines():
        if line.startswith('# HELP '):
            parts = line.split(' ', 3)
            if len(parts) == 4:
                help_map[parts[2]] = parts[3]
        elif line.startswith('# TYPE '):
            parts = line.split(' ', 3)
            if len(parts) == 4:
                type_map[parts[2]] = parts[3]
    return [(name, type_map[name], help_map.get(name, "")) for name in type_map]


def update_schema(raw):
    """Update the cached metric schema from a live scrape response."""
    global _metric_schema
    parsed = parse_schema(raw)
    if parsed:
        _metric_schema = parsed


def get_schema():
    """Return the live schema if available, otherwise the hardcoded fallback."""
    return _metric_schema or FALLBACK_METRICS


def zero_metrics(label):
    """Emit all known metrics with value 0 for an unloaded model."""
    lines = []
    for name, typ, help_text in get_schema():
        lines.append(f'# HELP {name} {help_text}')
        lines.append(f'# TYPE {name} {typ}')
        lines.append(f'{name}{{model="{label}"}} 0')
    return '\n'.join(lines) + '\n'


def inject_model_label(text, label):
    """Inject model="<label>" into every metric line of a Prometheus text payload."""
    lines = []
    for line in text.splitlines():
        if line.startswith('#') or not line.strip():
            lines.append(line)
            continue
        if '{' in line:
            # existing labels: metric{foo="bar"} -> metric{model="label",foo="bar"}
            line = line.replace('{', f'{{model="{label}",', 1)
        else:
            # no labels: metric 1.0 -> metric{model="label"} 1.0
            parts = line.split(' ', 1)
            if len(parts) == 2:
                line = f'{parts[0]}{{model="{label}"}} {parts[1]}'
        lines.append(line)
    return '\n'.join(lines) + '\n'


def scrape_model(base_url, model_id):
    """Fetch raw Prometheus text from a single model instance. Returns empty string on failure."""
    url = f"{base_url}/metrics?model={urllib.parse.quote(model_id, safe='/')}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read().decode()
    except Exception as e:
        print(f"warn: failed to scrape {model_id}: {e}")
        return ""


class MetricsHandler(BaseHTTPRequestHandler):
    def __init__(self, base_url, *args, **kwargs):
        self.base_url = base_url
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.path != '/metrics':
            self.send_response(404)
            self.end_headers()
            return

        try:
            models = get_models(self.base_url)
        except Exception as e:
            print(f"error: failed to fetch models: {e}")
            self.send_response(502)
            self.end_headers()
            return

        parts = []
        for model_id, label, loaded in models:
            if loaded:
                raw = scrape_model(self.base_url, model_id)
                if raw:
                    update_schema(raw)
                    parts.append(inject_model_label(raw, label))
                else:
                    parts.append(zero_metrics(label))
            else:
                parts.append(zero_metrics(label))

        body = '\n'.join(parts).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; version=0.0.4')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")


def main():
    parser = argparse.ArgumentParser(description="llama.cpp multi-model Prometheus metrics proxy")
    parser.add_argument('--url', default='http://localhost:8080', help='llama.cpp server base URL')
    parser.add_argument('--port', type=int, default=9090, help='port to serve metrics on')
    args = parser.parse_args()

    handler = partial(MetricsHandler, args.url)
    server = HTTPServer(('', args.port), handler)
    print(f"scraping {args.url}, serving /metrics on :{args.port}")
    server.serve_forever()


if __name__ == '__main__':
    main()
