
```mermaid
flowchart LR
    subgraph Part A: Telemetry Ingestion Pipeline
        A[AgentGuard Python SDK<br/>demo/run_demo.py] -->|POST JSON Spans| B(Splunk HEC<br/>Port: 8088)
        B -->|Route to Index| C[(Splunk Enterprise<br/>Index: main<br/>Sourcetype: agentguard:trace)]
    end

    subgraph Part B: AI Diagnostic Workflow
        D[Claude Desktop App<br/>AI Agent] -->|npx mcp-remote via HTTP| E(Splunk MCP Server App<br/>Port: 8089)
        E -->|Execute SPL Query| C
        C -.->|Return JSON Results| E
        E -.->|Return Diagnostic Payload| D
    end

    %% Minimal Styling (Let GitHub handle the default theme)
    classDef database fill:#000000,stroke:#4CAF50,stroke-width:2px,color:#fff;
    class C database;
