"""include_fields / exclude_fields handling (Bugzilla::WebService::Util::filter)."""


def split_fields(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out = []
        for v in value:
            out.extend(split_fields(v))
        return out
    import re
    return [f for f in re.split(r"[\s+,]", str(value)) if f]


class Filter:
    def __init__(self, include=None, exclude=None):
        self.include = set(split_fields(include))
        self.exclude = set(split_fields(exclude))
        self.include_types = {k[1:] for k in self.include if k.startswith("_")}
        self.exclude_types = {k[1:] for k in self.exclude if k.startswith("_")}
        self.include -= {k for k in self.include if k.startswith("_")}
        self.exclude -= {k for k in self.exclude if k.startswith("_")}
        if not self.include and not self.include_types:
            self.include_types.add("default")

    @classmethod
    def from_params(cls, params: dict) -> "Filter":
        return cls(params.get("include_fields"), params.get("exclude_fields"))

    def wants(self, field: str, types=("default",), prefix: str | None = None) -> bool:
        if prefix:
            field = f"{prefix}.{field}"
        types = (types,) if isinstance(types, str) else tuple(types or ("default",))
        if field in self.exclude:
            return False
        if field in self.include:
            return True
        if "all" in self.exclude_types:
            return False
        if "all" in self.include_types:
            return True
        for t in types:
            if t in self.exclude_types:
                return False
            if t in self.include_types:
                return True
        if prefix:
            return prefix in self.include
        key = field + "."
        return any(k.startswith(key) for k in self.include)

    def apply(self, item: dict, types=("default",), prefix: str | None = None) -> dict:
        return {k: v for k, v in item.items() if self.wants(k, types, prefix)}
