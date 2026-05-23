# AgentGuard Splunk App

Install on Splunk Enterprise / Cloud. Indexes events with `sourcetype=agentguard:trace`.

## Install

1. Copy or symlink this folder into `$SPLUNK_HOME/etc/apps/agentguard`
2. Restart Splunk (or install via **Manage Apps → Install app from file** after packaging)
3. Open **AgentGuard Overview** dashboard under Apps

```bash
# Development install (Splunk Enterprise)
cp -R splunk_app/default $SPLUNK_HOME/etc/apps/agentguard/default
# Or rename splunk_app → agentguard and place under etc/apps/
```

## Packaged contents

| Path | Purpose |
|------|---------|
| `default/app.conf` | App metadata |
| `default/savedsearches.conf` | Three SPL saved searches |
| `default/data/ui/views/agentguard_overview.xml` | Dashboard: failures, health, latency |

## Saved searches

### agentguard_failures_15m
```spl
index=main sourcetype=agentguard:trace status=FAILED earliest=-15m
| stats count by agent_name, error_type
| sort -count
```

### agentguard_latency_p95
```spl
index=main sourcetype=agentguard:trace earliest=-1h
| stats p95(latency_ms) as p95_latency_ms by agent_name
| sort -p95_latency_ms
```

### agentguard_error_breakdown
```spl
index=main sourcetype=agentguard:trace (status=FAILED OR status=TIMEOUT) earliest=-24h
| stats count by error_type, agent_name
```

## HEC setup

1. Settings → Data Inputs → HTTP Event Collector → New Token
2. Source type: `agentguard:trace` (or allow auto)
3. Export:
   ```bash
   export SPLUNK_HEC_URL=https://localhost:8088
   export SPLUNK_HEC_TOKEN=<token>
   ```

## Dashboard panels

- **Failures (15m)** — `timechart` of FAILED spans by `agent_name`
- **Agent health** — table from `agentguard_error_breakdown`
- **P95 latency** — bar chart from `agentguard_latency_p95`
- **Recent failed spans** — trace_id, error_type, tool_name

## SDK quick test

```bash
python demo/run_demo.py --cycles 3
python demo/inject_failure.py
```

Search in Splunk:

```spl
index=main sourcetype=agentguard:trace status=FAILED
```
