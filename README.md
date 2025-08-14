# homelab-dns-tracer

A simple DNS latency exporter for Prometheus/Grafana, designed for homelab DNS chains.

**Measures per-hop, chain, and entrypoint DNS latency for any set of queries and servers.**

---

## Features

- **Per-hop DNS latency:** Measure how fast each DNS server responds to queries.
- **Chain latency:** Calculate the total time if a query was sent to each server in sequence.
- **Entrypoint latency:** Measure "real" client latency by probing only the entrypoint (e.g., CoreDNS).
- **Prometheus metrics endpoint** for easy Grafana dashboards.
- **Configurable via YAML**.

---

## Quickstart

#### 1. Clone the repo

```sh
git clone https://github.com/YOURUSER/homelab-dns-tracer.git
cd homelab-dns-tracer
```

### 2. Install dependencies

You can use uv for fast, modern Python dependency management:

```sh
uv sync
```

Or use pip with the included requirements.txt:

```sh
pip install -r requirements.txt
```

#### 3. Configure your servers and queries

Edit `config.yaml` with your DNS servers and queries.

Example:

```yaml
interval: 60
listen_port: 9115

servers:
  - name: adguard
    address: 192.168.86.214
  - name: unbound
    address: 192.168.86.156
  - name: coredns
    address: 192.168.86.220

entrypoints:
  - name: coredns
    address: 192.168.86.220

queries:
  - name: google.com
    type: A
  - name: accounts.google.com
    type: AAAA
  - name: n8n.timmybtech.com
    type: AAAA
  - name: n8n.local.timmybtech.com
    type: A
```

#### 4. Run the exporter

```sh
python dns-tracer.py
```

You should see log output and the Prometheus exporter running at `http://localhost:9115/metrics`.

## Metrics

- **dns_trace_latency_seconds{query_name,query_type,hop,hop_index}** — per-hop latency
- **dns_trace_chain_latency_seconds{query_name,query_type}** — sum of all hops
- **dns_trace_entrypoint_latency_seconds{query_name,query_type,entrypoint}** — entrypoint (end-to-end) latency
- **dns_trace_probe_failed_total**{query_name,query_type,hop} - per-hop failures

## Using with Prometheus & Grafana

Add your exporter to Prometheus:

```yaml
- job_name: 'dns-tracer'
  static_configs:
    - targets: ['localhost:9115']
```

- Explore and graph metrics in Grafana.
- See example Prometheus queries below.

## Example Prometheus Queries

- Average entrypoint latency per query:

```promql
avg by (query_name) (dns_trace_entrypoint_latency_seconds)
```

- Per-hop latency:

```promql
avg by (hop) (dns_trace_latency_seconds)
```

- Chain latency:

```promql
avg(dns_trace_chain_latency_seconds)
```

## FAQ

Q: Why do some queries fail for unbound?
A: If you use DNS rewrites (e.g., CNAMEs or local zones) in CoreDNS/AdGuard, but not Unbound, queries for internal-only names may fail when probing Unbound directly. This is expected.

Q: Can I use this for external DNS?
A: Yes! Just add public resolvers and public queries to your config.

## Requirements

- Python 3.8+
- dnspython
- PyYAML
- prometheus_client
- All dependencies are listed in pyproject.toml and requirements.txt.

## License

MIT or your choice.

## PRs and improvements welcome!

Questions? Open an issue or ask in the repo.
