import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import dns.resolver
import yaml
from prometheus_client import Gauge, Counter, start_http_server

CONFIG_FILE = os.environ.get("DNS_EXPORTER_CONFIG", "config.yaml")

# --- Logging setup ---
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=os.environ.get("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger("dnstrace-exporter")


# --- Config validation ---
def validate_config(config):
    """Validate config file"""
    errors = []
    if not isinstance(config, dict):
        errors.append("Top-level config must be a mapping/object.")

    for field in ["servers", "queries"]:
        if field not in config:
            errors.append(f"Missing required field: '{field}'")
        elif not isinstance(config[field], list) or not config[field]:
            errors.append(f"'{field}' must be a non-empty list.")

    if "servers" in config:
        for i, server in enumerate(config["servers"]):
            if not isinstance(server, dict):
                errors.append(f"servers[{i}] must be a mapping/object.")
            else:
                errors.extend(
                    f"servers[{i}] missing field: '{f}'"
                    for f in ["name", "address"]
                    if f not in server
                )
    if "queries" in config:
        for i, query in enumerate(config["queries"]):
            if not isinstance(query, dict):
                errors.append(f"queries[{i}] must be a mapping/object.")
            else:
                errors.extend(
                    f"queries[{i}] missing field: '{f}'"
                    for f in ["name", "type"]
                    if f not in query
                )
    if "entrypoints" in config:
        for i, entry in enumerate(config["entrypoints"]):
            if not isinstance(entry, dict):
                errors.append(f"entrypoints[{i}] must be a mapping/object.")
            else:
                errors.extend(
                    f"entrypoints[{i}] missing field: '{f}'"
                    for f in ["name", "address"]
                    if f not in entry
                )

    if errors:
        for err in errors:
            logger.error(f"CONFIG ERROR: {err}")
        raise ValueError("Config validation failed. See errors above.")


def load_config():
    """Load and validate config file"""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            validate_config(config)
            logger.info(f"Loaded config from {CONFIG_FILE}")
            return config
    except Exception as e:
        logger.exception(f"Failed to load or validate config: {e}")
        raise


# --- DNS probe ---
def probe_dns(server_addr, query, qtype):
    """Resolve a DNS query, timing out after 5 seconds"""
    resolver = dns.resolver.Resolver(configure=False)
    resolver.nameservers = [server_addr]
    try:
        start = time.time()
        resolver.resolve(query, qtype, lifetime=5)
        duration = time.time() - start
        logger.debug(f"Success: {query} ({qtype}) via {server_addr} in {duration:.3f}s")
        return duration, False  # False = not failed
    except Exception as e:
        logger.warning(f"DNS probe failed: {query} ({qtype}) via {server_addr}: {e}")
        return None, True  # True = failed


class DNSTraceExporter:
    def __init__(self, config):
        self.config = config
        self.reload_config()
        self.metrics = {
            "latency": Gauge(
                "dns_trace_latency_seconds",
                "DNS Query Latency per Hop",
                ["query_name", "query_type", "hop", "hop_index"],
            ),
            "chain_latency": Gauge(
                "dns_trace_chain_latency_seconds",
                "Total DNS Query Chain Latency",
                ["query_name", "query_type"],
            ),
            "entrypoint_latency": Gauge(
                "dns_trace_entrypoint_latency_seconds",
                "DNS Query Entrypoint Latency",
                ["query_name", "query_type", "entrypoint"],
            ),
            "probe_failed_total": Counter(
                "dns_trace_probe_failed_total",
                "Total failed DNS probes per Hop",
                ["query_name", "query_type", "hop", "hop_index"],
            ),
        }
        max_threads = max(4, len(self.queries) * len(self.servers))
        self.executor = ThreadPoolExecutor(max_workers=max_threads)

    def reload_config(self):
        self.interval = int(self.config.get("interval", 30))
        self.listen_port = int(self.config.get("listen_port", 9115))
        self.servers = self.config["servers"]
        self.queries = self.config["queries"]
        self.entrypoints = self.config.get("entrypoints", [])

    def probe_chain(self, query):
        # Probe all hops for a single query, in parallel
        futures = []
        results = {}
        for idx, server in enumerate(self.servers):
            future = self.executor.submit(
                probe_dns, server["address"], query["name"], query["type"]
            )
            futures.append((idx, server, future))
        chain_total = 0.0
        for idx, server, fut in futures:
            latency, failed = fut.result()
            label_args = dict(
                query_name=query["name"],
                query_type=query["type"],
                hop=server["name"],
                hop_index=str(idx),
            )
            if not failed and latency is not None:
                self.metrics["latency"].labels(**label_args).set(latency)
                results[(server["name"], idx)] = latency
                chain_total += latency
            else:
                self.metrics["probe_failed_total"].labels(**label_args).inc()
                logger.debug(
                    f"Probe failure counted: {query['name']} ({query['type']}) via {server['name']}"
                )
        # Set total chain latency (only sum of successful hops)
        self.metrics["chain_latency"].labels(
            query_name=query["name"], query_type=query["type"]
        ).set(chain_total)
        logger.info(
            f"Chain: {query['name']} ({query['type']}): total {chain_total:.3f}s | hops: {[f'{self.servers[i]['name']}:{l:.3f}s' for (n, i), l in results.items()]}"
        )

    def probe_entrypoints(self, query):
        for entry in self.entrypoints:
            latency, failed = probe_dns(entry["address"], query["name"], query["type"])
            if not failed and latency is not None:
                self.metrics["entrypoint_latency"].labels(
                    query_name=query["name"],
                    query_type=query["type"],
                    entrypoint=entry["name"],
                ).set(latency)
                logger.info(
                    f"Entrypoint: {query['name']} ({query['type']}): {entry['name']} {latency:.3f}s"
                )
            else:
                # Optionally, you could add a failure counter for entrypoints too.
                logger.debug(
                    f"Entrypoint probe failure: {query['name']} ({query['type']}) via {entry['name']}"
                )

    def run_probe(self):
        logger.info("Starting probe round.")
        futures = []
        for query in self.queries:
            # Each query chain is independent, so can be parallelized too
            fut = self.executor.submit(self.probe_chain, query)
            futures.append(fut)
            self.probe_entrypoints(query)
        # Wait for all query chains to finish
        for _ in as_completed(futures):
            pass
        logger.info("Probe round complete.")

    def loop(self):
        while True:
            try:
                self.run_probe()
            except Exception:
                logger.exception("Error during probe round:")
            time.sleep(self.interval)


def main():
    config = load_config()
    exporter = DNSTraceExporter(config)
    start_http_server(exporter.listen_port)
    logger.info(f"Exporter running, metrics at :{exporter.listen_port}/metrics")
    t = threading.Thread(target=exporter.loop)
    t.start()
    t.join()


if __name__ == "__main__":
    main()
