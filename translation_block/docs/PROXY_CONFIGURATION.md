# Proxy Configuration Guide

This guide explains how to configure PDFMathTranslate to work with custom OpenAI-compatible proxies.

## Quick Start

### Using Grok via Custom Proxy

1. Edit your configuration file:
   ```bash
   nano ~/.config/PDFMathTranslate/config.json
   ```

2. Add the grok translator configuration:
   ```json
   {
       "translators": [
           {
               "name": "grok",
               "envs": {
                   "GROK_BASE_URL": "http://your-proxy:8000/v1",
                   "GROK_API_KEY": "your-api-key",
                   "GROK_MODEL": "grok-4",
                   "GROK_STREAM": "false"
               }
           }
       ]
   }
   ```

3. Run translation:
   ```bash
   pdf2zh input.pdf --service grok -o ./output
   ```

## Configuration Options

### Grok Translator

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GROK_BASE_URL` | No | `https://api.x.ai/v1` | API endpoint URL |
| `GROK_API_KEY` | Yes | - | API authentication key |
| `GROK_MODEL` | No | `grok-2-1212` | Model name |
| `GROK_STREAM` | No | `true` | Enable streaming mode |

### OpenAIliked Translator

For custom proxies that don't support streaming:

```json
{
    "name": "openailiked",
    "envs": {
        "OPENAILIKED_BASE_URL": "http://your-proxy:8000/v1",
        "OPENAILIKED_API_KEY": "your-api-key",
        "OPENAILIKED_MODEL": "grok-4",
        "OPENAILIKED_STREAM": "false"
    }
}
```

### OpenAI Translator

```json
{
    "name": "openai",
    "envs": {
        "OPENAI_BASE_URL": "https://api.openai.com/v1",
        "OPENAI_API_KEY": "your-api-key",
        "OPENAI_MODEL": "gpt-4o-mini",
        "OPENAI_STREAM": "true"
    }
}
```

## Troubleshooting

### Error: "Model not found"

**Cause**: The proxy doesn't recognize the model name.

**Solution**: Check available models:
```bash
curl http://your-proxy:8000/v1/models \
  -H "Authorization: Bearer your-api-key"
```

Then update `GROK_MODEL` to a valid model name.

### Error: "'str' object has no attribute 'choices'"

**Cause**: The proxy returns streaming format, but the code expects non-streaming.

**Solution**: Set `*_STREAM` to `"false"`:
```json
{
    "GROK_STREAM": "false"
}
```

### Error: "Connection error" or "404 Not Found"

**Cause**: Incorrect base URL.

**Solution**: Verify the URL ends with `/v1`:
```json
{
    "GROK_BASE_URL": "http://your-proxy:8000/v1"
}
```

### Error: "Missing authentication token"

**Cause**: API key not configured or incorrect.

**Solution**: Verify your API key in the configuration.

## Example Configurations

### grok2api Proxy

```json
{
    "name": "grok",
    "envs": {
        "GROK_BASE_URL": "http://104.248.73.236:8000/v1",
        "GROK_API_KEY": "xiaoyibao@1234",
        "GROK_MODEL": "grok-4",
        "GROK_STREAM": "false"
    }
}
```

### Official X.AI (Streaming Enabled)

```json
{
    "name": "grok",
    "envs": {
        "GROK_API_KEY": "your-xai-api-key",
        "GROK_MODEL": "grok-2-1212",
        "GROK_STREAM": "true"
    }
}
```

### Local Ollama (via OpenAI-compatible endpoint)

```json
{
    "name": "openailiked",
    "envs": {
        "OPENAILIKED_BASE_URL": "http://localhost:11434/v1",
        "OPENAILIKED_API_KEY": "ollama",
        "OPENAILIKED_MODEL": "llama3",
        "OPENAILIKED_STREAM": "false"
    }
}
```

## Environment Variables

You can also set configuration via environment variables:

```bash
export GROK_BASE_URL="http://your-proxy:8000/v1"
export GROK_API_KEY="your-api-key"
export GROK_MODEL="grok-4"
export GROK_STREAM="false"

pdf2zh input.pdf --service grok
```

Environment variables take priority over `config.json` settings.

## Complete Example

```json
{
    "USE_MODELSCOPE": "0",
    "PDF2ZH_LANG_FROM": "English",
    "PDF2ZH_LANG_TO": "Simplified Chinese",
    "translators": [
        {
            "name": "grok",
            "envs": {
                "GROK_BASE_URL": "http://your-proxy:8000/v1",
                "GROK_API_KEY": "your-api-key",
                "GROK_MODEL": "grok-4",
                "GROK_STREAM": "false"
            }
        },
        {
            "name": "openailiked",
            "envs": {
                "OPENAILIKED_BASE_URL": "http://your-proxy:8000/v1",
                "OPENAILIKED_API_KEY": "your-api-key",
                "OPENAILIKED_MODEL": "grok-4",
                "OPENAILIKED_STREAM": "false"
            }
        }
    ],
    "ENABLED_SERVICES": ["grok", "openailiked"]
}
```

## Notes

- Streaming mode (`"true"`) provides faster response perception but may have compatibility issues with some proxies
- Non-streaming mode (`"false"`) is more compatible but waits for the complete response
- Always include `/v1` at the end of your base URL
- Use `OPENAILIKED` service for maximum compatibility with custom proxies
