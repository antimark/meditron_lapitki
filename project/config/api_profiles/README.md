# API profiles

Copy one of these TOML files and fill `base_url`, `model`, and `api_key`.
All profiles use the same case-first prompt in `../api_system_prompt.txt` and the same OpenAI-compatible `/chat/completions` adapter.

Run a profile directly:

```bash
python process_one.py document.md --config config/api_profiles/provider_1.toml
```
