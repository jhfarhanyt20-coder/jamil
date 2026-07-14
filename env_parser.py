"""Parses the raw text contents of a `.env`-style file into a key/value map.
Mirrors artifacts/api-server/src/lib/env-parser.ts in the web app so both
versions accept identically-formatted credential blocks."""


def parse_env_text(content: str) -> dict:
    result = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            value = value[1:-1]
        result[key] = value
    return result
