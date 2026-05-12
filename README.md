# cc-gemini-router

A lightweight proxy that lets **Claude Code** talk to **Google Gemini** by translating between the Anthropic Messages API format and the Gemini `generateContent` API.

```
Claude Code  →  ANTHROPIC_BASE_URL  →  this proxy (port 9090)  →  Gemini API
```

## Features

- Full streaming support (SSE) with correct Anthropic event sequence
- Tool use / function calling (both directions)
- Extended thinking (`thinking: {type: "enabled"}`) → Gemini `thinkingConfig`
- Image content (base64 and URL)
- System prompts, stop sequences, temperature, top_p/top_k
- Routes mounted at both `/v1/*` and `/gemini/v1/*` — works whether Cloudflare strips the path prefix or not
- Optional proxy API key (`PROXY_API_KEY`) for an extra auth layer

## Quick start

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env and set GEMINI_API_KEY

# 3. Start
bash start.sh
# or: python main.py
```

## Configure Claude Code

```bash
# In your shell profile or before running Claude Code:
export ANTHROPIC_BASE_URL=https://ai.vseek.app/gemini
export ANTHROPIC_API_KEY=dummy   # required by the SDK but not validated by the proxy

# Pick any Gemini model:
claude --model gemini-2.5-pro
claude --model gemini-2.5-flash
claude --model gemini-2.0-flash
```

## Cloudflare tunnel

If you're mapping `https://ai.vseek.app/gemini` → `localhost:9090`, the tunnel forwards the full path so the proxy receives `/gemini/v1/messages`. Both `/v1/messages` and `/gemini/v1/messages` are handled correctly.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes | — | Google AI Studio API key |
| `PROXY_API_KEY` | No | (empty) | If set, clients must send this as `x-api-key` or `Authorization: Bearer <key>` |
| `PORT` | No | `9090` | Port to listen on |

## Supported models

| Model slug | Notes |
|---|---|
| `gemini-2.5-pro` | Latest Gemini 2.5 Pro |
| `gemini-2.5-pro-preview-05-06` | Dated preview |
| `gemini-2.5-flash` | Fast, cost-effective |
| `gemini-2.5-flash-preview-04-17` | Dated preview |
| `gemini-2.0-flash` | Stable 2.0 release |
| `gemini-2.0-flash-thinking-exp` | 2.0 with thinking |
| `gemini-1.5-pro` | 1.5 series |
| `gemini-1.5-flash` | 1.5 series fast |

Any model name you pass is forwarded directly to Gemini, so newer slugs work automatically.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/messages` | Main chat endpoint |
| `POST` | `/gemini/v1/messages` | Same, with prefix |
| `GET` | `/v1/models` | List models |
| `GET` | `/gemini/v1/models` | Same, with prefix |
| `GET` | `/health` | Health check |
