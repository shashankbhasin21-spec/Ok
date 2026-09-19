# MCP Setup

The MCP server is a thin client over the gateway. It does not run inference.

```bash
export VIDEO_GATEWAY_URL=http://127.0.0.1:8080
export API_KEY=your-key
python -m mcp_server.server
```

## Tools

| Tool | Purpose |
|------|---------|
| `video_generate` | Text → video via gateway |
| `video_generate_from_image` | Image → video |
| `video_extend` | Extend video (routes to LTX when ready) |
| `video_status` | Job status; READY only after QC |
| `video_cancel` | Cancel |
| `video_download` | Download URL for READY assets |
| `video_list_jobs` | List jobs |
| `video_provider_status` | Engine readiness |
| `video_gateway_health` | `/health` |
| `video_metrics` | Metrics |

## Cursor / Claude connector

Point the custom MCP command at `python -m mcp_server.server` with the env vars above.
All generation requests enter through the gateway — the single control plane for Instagram/YouTube automation.
