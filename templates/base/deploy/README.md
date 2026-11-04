# Deploy

The `local` adapter is a reference implementation: it runs configured build,
migrate and release commands, then requires a real `probe_url`.

To connect SSH or Kubernetes, implement `DeployPort` and register a new adapter.
Do not fake success by removing the probe.
