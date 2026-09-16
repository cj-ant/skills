# Pointing a harness at the proxy

Goal: requests go to `http://127.0.0.1:8484`, the key is a dummy, and the model is any Claude ID (the proxy rewrites it). Settings move between versions, so find the setting in the clone you pinned, don't trust a doc or memory.

This skill names no third-party harness on purpose. Settings and flags for a named product go stale, and a list of them reads as a verdict on those projects. Keep per-harness notes in your own `~/pt-audit/<harness>/` folder.

## Find the base-URL setting

```bash
grep -rniE 'base_?url|api_?base|api_?url|ANTHROPIC_[A-Z_]*(URL|HOST|BASE)|endpoint' <clone> | grep -vi test | head -40
```

It is one of these:

| Where the harness gets it | What to set |
|---|---|
| An Anthropic SDK with default options | `ANTHROPIC_BASE_URL=http://127.0.0.1:8484` and `ANTHROPIC_API_KEY=sk-ant-dummy` |
| Its own environment variable | the name the grep shows, set to the proxy URL |
| A config file (JSON, YAML, TOML) with a provider or model entry | the base-URL field of the Anthropic provider. Write the file into the harness's isolated config directory from your driver |
| An editor or GUI settings page | the "custom base URL" field of the Anthropic provider |
| Hard-coded | patch the constant on a local branch and say so in the report notes |

If the harness validates the key's format, use `sk-ant-api03-` followed by filler. If it logs in with OAuth, log out first so it falls back to the API key.

## The `/v1` suffix

Some client libraries append `/v1/messages` to the base URL, and some append only `/messages` and expect the base URL to end in `/v1`. The Anthropic SDKs are the first kind. `pt_run.sh` exports both forms: `PT_BASE_URL` and `PT_BASE_URL_V1`.

Start with `PT_BASE_URL`. A request for `/v1/v1/messages` in the harness's error output, or in `proxy.log`, means drop the suffix. A 404 on `/messages` means add it.

## Routes the proxy can't see

The proxy records the Anthropic Messages API only. A harness that reaches Claude through an OpenAI-compatible endpoint, a translating gateway, a cloud provider's SDK, or by wrapping another agent sends nothing the proxy can record. Audit those by code reading, and check whether the translation layer keeps the thinking block's `signature` (FID-1). Most don't.

## Model IDs and model switches

If a harness pins model IDs, pick any Claude model it knows. The proxy sends `claude-fable-5-1`. To test a model switch, restart the proxy with `--force-model ''` and give the harness real IDs for both models.

## Headless or interactive

A headless mode (a one-shot prompt flag, plus a flag that continues the last session) makes a run repeatable, and each "continue" is a real resume worth marking. Look for it in `--help` and the README. For a harness with only a TUI, drive it through `tmux`.
