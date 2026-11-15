# Choosing models and providers

Two things in a run can be a model: the **system under test** (when you test a
model directly, `subject.kind: llm` in project.yaml) and the **judge** (in the
judge-panel TOML). Both name a `provider` and a `model`. Neither needs a code
change to point at a different model or vendor.

```bash
clinical-ai-eval models check --project ./my-assessment   # offline, free
clinical-ai-eval models list  --project ./my-assessment   # free: which IDs your key can use
clinical-ai-eval connector test --project ./my-assessment # one real call to the subject
```

`models list` generates no tokens; the built-in providers do not bill for model
listing. It still needs each provider's key.

## Providers

Built in: `openai`, `anthropic`, `google`, `xai`. Each uses one of four API styles:

| API style | used by | endpoint |
|---|---|---|
| `openai_responses` | OpenAI, xAI | `POST {base_url}/responses` |
| `anthropic_messages` | Anthropic | `POST {base_url}/messages` |
| `google_generate_content` | Google Gemini | `POST {base_url}/models/{model}:generateContent` |
| `openai_chat_completions` | anything OpenAI-compatible | `POST {base_url}/chat/completions` |

`openai_chat_completions` covers most other vendors (DeepSeek, Mistral, and so
on), aggregators (OpenRouter, Together, Fireworks), company gateways, and local
open-weight servers (vLLM, Ollama, llama.cpp).

### Defining a provider

In a judge panel (TOML):

```toml
[providers.deepseek]
api = "openai_chat_completions"
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
vendor = "deepseek"

[[judges]]
name = "deepseek-judge"
provider = "deepseek"
model = "<id from models list>"
reasoning_effort = "high"
mock = false
```

For a subject (project.yaml), the same fields go under `subject.providers`:

```yaml
subject:
  kind: llm
  provider: deepseek
  model: "<id from models list>"
  reasoning_effort: high
  providers:
    deepseek:
      api: openai_chat_completions
      base_url: https://api.deepseek.com/v1
      api_key_env: DEEPSEEK_API_KEY
```

| field | default | notes |
|---|---|---|
| `api` | required for a new provider | one of the four styles above |
| `base_url` | required for a new provider | an `http(s)://` URL, without the endpoint path |
| `api_key_env` | required for a remote server | empty (`""`) for a keyless local server |
| `effort_param` | `reasoning_effort` | empty if the server rejects that field |
| `max_tokens_field` | `max_tokens` | e.g. `max_completion_tokens` |
| `vendor` | the provider name | the model's developer (see below) |

A built-in can be redirected field by field, for example to a regional endpoint
or a gateway that speaks the same API:

```toml
[providers.anthropic]
base_url = "https://gateway.example.org/anthropic/v1"
```

### Keys

Keys come from environment variables named by `api_key_env`, or from a
git-ignored keys file (`API_KEYS.local.md` beside the package, or the path in
`MEDROBUST_KEYS_PATH`) holding lines like `DEEPSEEK_API_KEY = ...`. Environment
variables alone are enough. A key is never put in a URL, a log line or an output
file, and a missing key fails with its variable name when it is first needed.

## Reasoning effort

Set `reasoning_effort` explicitly. Omitting it means "high" for judges and
subjects; `provider_default` sends nothing and leaves the choice to the provider,
whose default can change between model versions (Claude Opus 5.5, for example,
defaults to `medium` where Claude Opus 5 defaulted to `high`).

| API style | accepted values |
|---|---|
| `openai_responses` | `minimal` `low` `medium` `high` `xhigh` |
| `anthropic_messages` | `low` `medium` `high` `xhigh` `max` |
| `google_generate_content` | `low` `medium` `high` (sent as `thinkingLevel`) |
| `openai_chat_completions` | `minimal` `low` `medium` `high`, if the server takes `reasoning_effort` |

These are checked offline per API style. A particular model may accept fewer
levels; `connector test` is where that shows up.

## `vendor` and judge diversity

`min_distinct_providers` counts **model developers**, taken from `vendor` (or the
provider's own `vendor`). Two models reached through one aggregator are not two
independent judges, and the same developer's model reached through two hosts is
not two either. When several models come through one aggregator, set `vendor` on
each judge:

```toml
[[judges]]
name = "via-aggregator"
provider = "openrouter"
model = "anthropic/claude-opus-5-5"
vendor = "anthropic"
mock = false
```

If the judge and the system under test share a developer, say so in what you
report: a panel does not remove that confound.

## What is recorded per call

Configured and provider-reported model ID (a mismatch is flagged), API style,
endpoint, reasoning setting, token cap, request hash, HTTP status, attempts,
finish reason, refusal details, timing and usage. Error bodies from providers are
not recorded, because they can echo the clinical prompt. No provider-side
fallback to a different model is requested: if a model declines, that is the
configured model's outcome.
