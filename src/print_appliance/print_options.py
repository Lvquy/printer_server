"""Public print vocabulary. Only explicit, verified driver enum mappings are exposed."""

from typing import Any

FIELDS = ("paper_size", "sides", "scaling")
MAPPINGS = {
    "paper_size": {
        "PageSize": {v: v for v in ("A3", "A4", "A5", "A6", "Letter", "Legal", "Executive")}
    },
    "sides": {
        "Duplex": {
            "None": "one-sided",
            "DuplexNoTumble": "two-sided-long-edge",
            "DuplexTumble": "two-sided-short-edge",
        }
    },
    "scaling": {"print-scaling": {v: v for v in ("auto", "auto-fit", "fit", "fill", "none")}},
}


class InvalidPrintOption(ValueError):
    def __init__(self, field: str, value: Any, allowed: list[str], message: str):
        self.detail = {
            "code": "invalid_print_option",
            "field": field,
            "value": value,
            "allowed": allowed,
            "message": message,
        }
        super().__init__(message)


def public_capabilities(schema: dict, defaults: dict, formats: list[str]) -> tuple[dict, dict]:
    supported = {field: [] for field in FIELDS}
    public_defaults = {field: None for field in FIELDS}
    translations: dict = {}
    available = schema.get("availability", "unknown")
    if available in {"available", "partial"} and "pdf" in formats and not schema.get("raw"):
        descriptors = {item["name"]: item for item in schema.get("options", [])}
        for field, candidates in MAPPINGS.items():
            for name, mapping in candidates.items():
                descriptor = descriptors.get(name)
                if not descriptor:
                    continue
                values = {c["value"] for c in descriptor.get("choices", [])}
                # Ambiguous enum translations are not advertised (e.g. RGB and CMYK).
                reverse: dict[str, list[str]] = {}
                for native, public in mapping.items():
                    if native in values:
                        reverse.setdefault(public, []).append(native)
                translations[field] = {
                    public: (name, native[0])
                    for public, native in reverse.items()
                    if len(native) == 1
                }
                supported[field] = list(translations[field])
                public_defaults[field] = mapping.get(defaults.get(name, descriptor.get("default")))
                break
    return {
        "availability": available,
        "reason": schema.get("reason"),
        "supported": supported,
        "defaults": public_defaults,
    }, translations


def translate_options(requested: dict, schema: dict, defaults: dict, format_name: str) -> dict:
    capabilities, translations = public_capabilities(schema, defaults, [format_name])
    result = {}
    for field, value in requested.items():
        allowed = capabilities["supported"].get(field, [])
        if not isinstance(value, str) or value not in allowed:
            raise InvalidPrintOption(
                field,
                value,
                allowed,
                "Unsupported print option; use an allowed value or omit this field.",
            )
        name, native = translations[field][value]
        result[name] = native
    return result
