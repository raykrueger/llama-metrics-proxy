#!/usr/bin/env python3
import argparse
import json
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from functools import partial


def get_models(base_url):
    with urllib.request.urlopen(f"{base_url}/v1/models", timeout=10) as resp:
        data = json.loads(resp.read())
    models = []
    for m in data.get("data", []):
        status = m.get("status", {})
        model_id = m["id"]
        aliases = m.get("aliases", [])
        label = aliases[0] if aliases else model_id
        loaded = status.get("value") == "loaded" and not status.get("failed")
        models.append((model_id, label, loaded))
    return models


# Parsed from a live scrape: list of (name, type, help_text)
_metric_schema = []

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
    """Extract (name, type, help) tuples from Prometheus text."""
    schema = []
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
    for name in type_map:
        schema.append((name, type_map[name], help_map.get(name, "")))
    return schema


def update_schema(raw):
    global _metric_schema
    parsed = parse_schema(raw)
    if parsed:
        _metric_schema = parsed


def get_schema():
    return _metric_schema or FALLBACK_METRICS


def zero_metrics(label):
    lines = []
    for name, typ, help_text in get_schema():
        lines.append(f'# HELP {name} {help_text}')
        lines.append(f'# TYPE {name} {typ}')
        lines.append(f'{name}{{model="{label}"}} 0')
    return '\n'.join(lines) + '\n'


def inject_model_label(text, label):
    lines = []
    for line in text.splitlines():
        if line.startswith('#') or not line.strip():
            lines.append(line)
            continue
        if '{' in line:
            line = line.replace('{', f'{{model="{label}",', 1)
        else:
            parts = line.split(' ', 1)
            if len(parts) == 2:
                line = f'{parts[0]}{{model="{label}"}} {parts[1]}'
        lines.append(line)
    return '\n'.join(lines) + '\n'


def scrape_model(base_url, model_id):
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
