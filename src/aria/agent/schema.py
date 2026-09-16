"""Small documented JSON Schema subset; no third-party validator required."""
import math

TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", dict: "object", list: "array"}


def object_schema(parameters):
    return {"type": "object", "properties": {k: {"type": TYPES[v]} for k, v in parameters.items()},
            "required": list(parameters), "additionalProperties": False}


def validate(value, schema, path="$"):
    if not schema:
        return
    if "anyOf" in schema:
        for candidate in schema["anyOf"]:
            try:
                validate(value, candidate, path)
                return
            except ValueError:
                pass
        raise ValueError(f"{path}: no matching schema")
    kind = schema.get("type")
    valid = {"string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float) and math.isfinite(value),
             "boolean": type(value) is bool, "object": isinstance(value, dict),
             "array": isinstance(value, list), "null": value is None}
    if kind is not None and not valid.get(kind, False):
        raise ValueError(f"{path}: expected {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: invalid enum value")
    if isinstance(value, str) and len(value.strip()) < schema.get("minLength", 0):
        raise ValueError(f"{path}: string too short")
    if type(value) in (int, float):
        if not math.isfinite(value) or value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ValueError(f"{path}: number outside range")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not set(schema.get("required", [])) <= value.keys():
            raise ValueError(f"{path}: missing required properties")
        if schema.get("additionalProperties") is False and value.keys() - properties.keys():
            raise ValueError(f"{path}: unexpected properties")
        for key, item in value.items():
            validate(item, properties.get(key, {}), f"{path}.{key}")
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", math.inf):
            raise ValueError(f"{path}: too many items")
        for i, item in enumerate(value):
            validate(item, schema.get("items", {}), f"{path}[{i}]")
