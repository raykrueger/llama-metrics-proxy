#!/usr/bin/env python3
"""
Prometheus metrics aggregator for llama.cpp router mode.

llama.cpp's /metrics endpoint requires a ?model= parameter when running in
router mode (--models-preset). Scraping without one returns a 400 error, and
scraping an unloaded model wakes it from sleep. This sidecar works around both
problems by discovering models via /v1/models, scraping only loaded ones, and
emitting zero-value metrics for unloaded models so time series remain continuous.

Output is structured per Prometheus exposition spec: each metric family's
# HELP and # TYPE lines appear exactly once, followed by samples from all models.
"""
import argparse
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from functools import partial


def get_models(base_url):
    """Return all non-failed models from /v1/models as (model_id, label, loaded) tuples.

    label is the first alias if available, otherwise the model id.
    loaded is True only when status.value == "loaded".
    Failed models are excluded entirely — they cannot serve metrics and would
    trigger repeated failed wakeup attempts on every scrape.
    Malformed entries missing an id are skipped.
    """
    with urllib.request.urlopen(f"{base_url}/v1/models", timeout=10) as resp:
        data = json.loads(resp.read())
    models = []
    for m in data.get("data", []):
        status = m.get("status", {})
        if status.get("failed"):
            continue
        model_id = m.get("id")
        if not model_id:
            continue
        aliases = m.get("aliases", [])
        label = aliases[0] if aliases else model_id
        loaded = status.get("value") == "loaded"
        models.append((model_id, label, loaded))
    return models


# Metric schema discovered from a live scrape: list of (name, type, help_text).
# Updated on every successful scrape so new metrics added by llama.cpp are
# picked up automatically without restarting this proxy.
# HTTPServer is single-threaded; no locking needed.
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
            if len(parts) >= 3:
                help_map[parts[2]] = parts[3] if len(parts) == 4 else ''
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


def parse_families(text, label):
    """Parse a Prometheus text payload into per-family dicts with model label injected.

    Returns {metric_name: {"help": str, "type": str, "samples": [str]}}
    """
    families = {}
    for line in text.splitlines():
        if not line.strip() or line == '# EOF':
            continue
        if line.startswith('# HELP '):
            parts = line.split(' ', 3)
            if len(parts) >= 3:
                name = parts[2]
                help_text = parts[3] if len(parts) == 4 else ''
                if name not in families:
                    families[name] = {'help': help_text, 'type': 'untyped', 'samples': []}
                else:
                    families[name]['help'] = help_text
        elif line.startswith('# TYPE '):
            parts = line.split(' ', 3)
            if len(parts) == 4:
                name, typ = parts[2], parts[3]
                if name not in families:
                    families[name] = {'help': '', 'type': typ, 'samples': []}
                else:
                    families[name]['type'] = typ
        elif not line.startswith('#'):
            metric_name = line.split('{')[0].split(' ')[0]
            if '{' in line:
                sample = line.replace('{', f'{{model="{label}",', 1)
            else:
                parts = line.split(' ', 1)
                if len(parts) == 2:
                    sample = f'{parts[0]}{{model="{label}"}} {parts[1]}'
                else:
                    continue
            if metric_name not in families:
                families[metric_name] = {'help': '', 'type': 'untyped', 'samples': []}
            families[metric_name]['samples'].append(sample)
    return families


def zero_families(label):
    """Generate zero-value metric families for an unloaded model."""
    families = {}
    for name, typ, help_text in get_schema():
        families[name] = {
            'help': help_text,
            'type': typ,
            'samples': [f'{name}{{model="{label}"}} 0'],
        }
    return families


def merge_families(*family_dicts):
    """Merge metric families from multiple models.

    HELP and TYPE are taken from the first source that defines them.
    Samples from all sources are concatenated in order.
    """
    merged = {}
    for fd in family_dicts:
        for name, data in fd.items():
            if name not in merged:
                merged[name] = {'help': data['help'], 'type': data['type'], 'samples': []}
            merged[name]['samples'].extend(data['samples'])
    return merged


def serialize_families(families):
    """Serialize merged metric families to Prometheus text format.

    Each family emits exactly one # HELP and one # TYPE line, per spec.
    """
    lines = []
    for name, data in families.items():
        if data['help']:
            lines.append(f"# HELP {name} {data['help']}")
        lines.append(f"# TYPE {name} {data['type']}")
        lines.extend(data['samples'])
    return '\n'.join(lines) + '\n'


def scrape_model(base_url, model_id):
    """Fetch raw Prometheus text from a single model instance. Returns empty string on failure."""
    url = f"{base_url}/metrics?model={urllib.parse.quote(model_id, safe='')}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read().decode()
    except Exception as e:
        print(f"warn: failed to scrape {model_id}: {e}")
        return ""


class MetricsHandler(BaseHTTPRequestHandler):
    def __init__(self, base_url, *args, **kwargs):
        self.base_url = base_url
        # NOTE: base class __init__ immediately calls handle() -> do_GET,
        # so self.base_url must be assigned before calling super().__init__().
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
            self.send_header('Content-Length', '0')
            self.end_headers()
            return

        all_families = []
        for model_id, label, loaded in models:
            if loaded:
                raw = scrape_model(self.base_url, model_id)
                if raw:
                    update_schema(raw)
                    all_families.append(parse_families(raw, label))
                else:
                    all_families.append(zero_families(label))
            else:
                all_families.append(zero_families(label))

        merged = merge_families(*all_families)
        body = serialize_families(merged).encode()

        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; version=0.0.4')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        print(f"{timestamp} {self.address_string()} - {fmt % args}")


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
