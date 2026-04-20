"""Universal recursive TMOS configuration parser.

Ported from f5-corkscrew's `src/universalParse.ts` and `src/digConfigs.ts`
(Apache License 2.0, Copyright 2014-2025 F5 Networks, Inc.). See the
top-level NOTICE file for full attribution.

Modifications versus the TypeScript upstream:
- Re-implemented in idiomatic Python with dataclass-free dict output.
- Dropped tmos-converter legacy paths (AS3/DO export, GTM topology pieces
  that rely on an event emitter).
- Added `list_partitions`, `list_apps`, `app_details` as thin wrappers so the
  webapp can drive an LTM app browser.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "parse_tmos_config",
    "list_partitions",
    "list_apps",
    "app_summary",
    "app_details",
]


_RULE_MARKERS = ("ltm rule", "gtm rule", "pem irule")


def _is_rule(line: str) -> bool:
    return any(m in line for m in _RULE_MARKERS)


def _count_char(s: str, ch: str) -> int:
    return s.count(ch)


def _count_indent(line: str) -> int:
    m = re.match(r"^( *)", line)
    return len(m.group(1)) if m else 0


def _remove_one_indent(arr: list[str]) -> list[str]:
    return [re.sub(r"^    ", "", line) for line in arr]


def _get_title(line: str) -> str:
    # "ltm pool /Common/web_pool {" → "ltm pool /Common/web_pool"
    return re.sub(r"\s?\{\s?\}?$", "", line).strip()


def _str_to_obj(line: str) -> dict[str, str]:
    parts = line.strip().split(" ")
    key = parts[0] if parts else ""
    return {key: " ".join(parts[1:])}


def _obj_to_arr(line: str) -> list[str]:
    m = re.search(r"\{\s*(.*?)\s*\}", line, re.DOTALL)
    if m and m.group(1):
        tokens = m.group(1).strip().split()
        return [t for t in tokens if t]
    return []


def _arr_to_multiline_str(chunk: list[str]) -> dict[str, str]:
    joined = "\n".join(chunk)
    m = re.match(r"^(\S+)\s+\"([\s\S]*)\"$", joined)
    if m:
        return {m.group(1): m.group(2)}
    first = chunk[0].strip() if chunk else ""
    key = first.split(" ")[0] if first else ""
    value = re.sub(rf"^{re.escape(key)}\s*", "", "\n".join(chunk))
    value = re.sub(r'^"|"$', "", value)
    return {key: value}


# ── grouping / bracket matching ───────────────────────────────────────────


def _group_objects(lines: list[str]) -> tuple[list[list[str]], Exception | None]:
    """Split a flat TMOS config into top-level object groups."""
    groups: list[list[str]] = []
    error: Exception | None = None

    try:
        i = 0
        n = len(lines)
        while i < n:
            current = lines[i]
            if not current:
                i += 1
                continue

            # One-line empty/pseudo-array object
            if "{" in current and "}" in current and not current.startswith(" "):
                groups.append([current])
                i += 1
                continue

            if (
                current.rstrip().endswith("{")
                and not current.startswith(" ")
                and not current.startswith("#")
            ):
                c = 0
                rule_flag = _is_rule(current)
                rule_line = current if rule_flag else ""
                bracket_count = 1
                opening = 1
                closing = 0

                while bracket_count != 0:
                    c += 1
                    if i + c >= n:
                        if opening != closing and rule_flag:
                            error = Exception(
                                f"iRule parsing error, check the following iRule: {rule_line}"
                            )
                        break
                    line = lines[i + c]
                    subcount = 0

                    stripped = line.strip()
                    is_skippable_rule_internal = (
                        rule_flag
                        and (
                            stripped.startswith("#")
                            or stripped.startswith("set")
                            or stripped.startswith("STREAM")
                        )
                    )

                    if not is_skippable_rule_internal:
                        updated = re.sub(r'\\"', "", stripped)
                        updated = re.sub(r'"[^"]*"', "", updated)
                        prev_char = ""
                        for ch in updated:
                            if prev_char != "\\":
                                if ch == "{":
                                    subcount += 1
                                    opening += 1
                                elif ch == "}":
                                    subcount -= 1
                                    closing += 1
                            prev_char = ch

                        if _is_rule(line):
                            c -= 1
                            bracket_count = 0
                            break
                        bracket_count += subcount

                groups.append(lines[i : i + c + 1])
                i += c + 1
                continue

            i += 1
    except Exception as exc:  # pragma: no cover - defensive
        error = exc

    return groups, error


# ── orchestrator (recursive parse of one group) ───────────────────────────


def _orchestrate(arr: list[str]) -> dict[str, Any]:
    if not arr:
        return {}
    key = _get_title(arr[0])
    original_body = "\n".join(arr[1:-1])
    # Remove opening and closing bracket lines
    arr = arr[1:-1] if len(arr) >= 2 else []

    obj: Any = {}

    if _is_rule(key):
        obj = "\n".join(arr)
    elif "monitor min" in key:
        obj = " ".join(s.strip() for s in arr).split(" ")
    elif "cli script" not in key and "sys crypto cert-order-manager" not in key:
        i = 0
        while i < len(arr):
            line = arr[i]
            if not line:
                i += 1
                continue

            if line.endswith("{") and len(arr) != 1:
                c = 0
                while (i + c) < len(arr) and arr[i + c] != "    }":
                    c += 1
                    if i + c >= len(arr):
                        raise ValueError(
                            f"Missing or mis-indented '}}' for line: '{line}'"
                        )
                sub_arr = _remove_one_indent(arr[i : i + c + 1])

                arr_idx = 0
                coerced: list[str] = []
                for sub in sub_arr:
                    if sub == "    {":
                        coerced.append(sub.replace("{", f"{arr_idx} {{"))
                        arr_idx += 1
                    else:
                        coerced.append(sub)

                obj.update(_orchestrate(coerced))
                i += c + 1
                continue

            stripped = line.strip()
            if stripped.replace(" ", "").endswith("{}"):
                obj[line.split("{", 1)[0].strip()] = {}
                i += 1
                continue

            if "{" in line and "}" in line and '"' not in line:
                obj[line.split("{", 1)[0].strip()] = _obj_to_arr(line)
                i += 1
                continue

            # Flag-style single word
            if (
                (" " not in stripped or re.match(r'^"[\s\S]*"$', stripped))
                and "}" not in line
            ):
                obj[stripped] = ""
                i += 1
                continue

            if _count_indent(line) == 4:
                quote_count = line.count('"')
                if quote_count % 2 == 1:
                    c = 1
                    while i + c < len(arr) and arr[i + c].count('"') % 2 != 1:
                        c += 1
                    chunk = arr[i : i + c + 1]
                    obj.update(_arr_to_multiline_str(chunk))
                    i += c + 1
                    continue

                tmp = _str_to_obj(stripped)
                if key.startswith("gtm monitor external") and "user-defined" in tmp:
                    obj.setdefault("user-defined", {})
                    sub = _str_to_obj(tmp["user-defined"])
                    if sub:
                        k2 = next(iter(sub))
                        obj["user-defined"][k2] = sub[k2]
                else:
                    obj.update(tmp)

            i += 1

    if isinstance(obj, dict):
        obj["_originalBody"] = original_body

    return {key: obj}


# ── pre-processing passes (GTM topology, comments) ────────────────────────


def _preprocess_topology(lines: list[str]) -> list[str]:
    new_lines: list[str] = []
    topology_arr: list[str] = []
    topology_count = 0
    longest_match = False
    in_topology = False

    for line in lines:
        if "topology-longest-match" in line and "yes" in line:
            longest_match = True

        if line.startswith("gtm topology ldns:"):
            in_topology = True
            if not topology_arr:
                topology_arr.append("gtm topology /Common/Shared/topology {")
                topology_arr.append("    records {")
            ldns_idx = line.index("ldns:")
            server_idx = line.index("server:")
            brace_idx = line.index("{")
            ldns = line[ldns_idx + 5 : server_idx].strip()
            topology_arr.append(f"        topology_{topology_count} {{")
            topology_count += 1
            topology_arr.append(f"            source {ldns}")
            server = line[server_idx + 7 : brace_idx].strip()
            topology_arr.append(f"            destination {server}")
        elif in_topology:
            if line == "}":
                in_topology = False
                topology_arr.append("        }")
            else:
                topology_arr.append(f"        {line}")
        else:
            new_lines.append(line)

    if topology_arr:
        topology_arr.append(f"        longest-match-enabled {str(longest_match).lower()}")
        topology_arr.append("    }")
        topology_arr.append("}")

    return new_lines + topology_arr


def _preprocess_comments(lines: list[str]) -> list[str]:
    irule_depth = 0
    out: list[str] = []
    for line in lines:
        if irule_depth == 0:
            if line.strip().startswith("# "):
                out.append(line.strip().replace("# ", "#comment# ", 1))
                continue
            if _is_rule(line):
                irule_depth += 1
        elif not line.strip().startswith("#"):
            irule_depth += _count_char(line, "{") - _count_char(line, "}")
        out.append(line)
    return out


# ── flat → hierarchical conversion ────────────────────────────────────────


def _flat_to_hierarchical(flat: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in flat.items():
        parts = key.split(" ")
        if len(parts) < 2:
            result[key] = value
            continue

        category = parts[0]
        result.setdefault(category, {})

        name_idx = next(
            (i for i, p in enumerate(parts) if p.startswith("/")), -1
        )
        if name_idx == -1:
            rest_key = " ".join(parts[1:])
            result[category][rest_key] = value
            continue

        path_parts = parts[1:name_idx]
        object_name = " ".join(parts[name_idx:])

        current: dict[str, Any] = result[category]
        for p in path_parts:
            current.setdefault(p, {})
            current = current[p]

        if isinstance(value, str):
            current[object_name] = value
            continue

        body = value.pop("_originalBody", "")
        enhanced: dict[str, Any] = dict(value)
        enhanced["line"] = body

        m = re.match(
            r"^(/[\w\d_\-.]+(?:/[\w\d_\-.]+)?)/([\w\d_\-.]+)$", object_name
        )
        if m:
            path_segment = m.group(1)
            segs = path_segment.split("/")
            enhanced["partition"] = segs[1]
            if len(segs) > 2:
                enhanced["folder"] = segs[2]
            enhanced["name"] = m.group(2)

        current[object_name] = enhanced

    return result


def _cleanup_original_body(obj: Any) -> Any:
    if not isinstance(obj, (dict, list)):
        return obj
    if isinstance(obj, list):
        return [_cleanup_original_body(x) for x in obj]
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if k == "_originalBody":
            out["line"] = v
        elif isinstance(v, (dict, list)):
            out[k] = _cleanup_original_body(v)
        else:
            out[k] = v
    return out


# ── public API ────────────────────────────────────────────────────────────


def parse_tmos_config(config_text: str) -> dict[str, Any]:
    """Parse raw TMOS configuration text into a hierarchical dict.

    Output shape matches f5-corkscrew's `configObject`:
        {
          "ltm": {
              "virtual": {"/Common/vs1": {"line": "...", "destination": "...", ...}},
              "pool":    {"/Common/p1":  {"line": "...", "members": {...}}},
              "node":    {...},
              "monitor": {"http": {"/Common/http": {...}}},
              "profile": {...},
              "rule":    {"/Common/r1": "when HTTP_REQUEST {...}"},
          },
          "gtm": {...},
          "sys": {...},
          ...
        }
    """
    normalized = config_text.replace("\r\n", "\n")
    lines = normalized.split("\n")

    lines = _preprocess_topology(lines)
    lines = _preprocess_comments(lines)
    lines = [l for l in lines if not (l == "" or l.strip().startswith("#comment# "))]

    groups, err = _group_objects(lines)
    if err is not None:
        raise err

    flat: dict[str, Any] = {}
    for group in groups:
        flat.update(_orchestrate(group))

    hierarchical = _flat_to_hierarchical(flat)
    return _cleanup_original_body(hierarchical)


def list_partitions(config: dict[str, Any]) -> list[str]:
    """Return the set of administrative partitions defined in this config.

    Sources, in order of authority:
      1. `auth partition <name> { ... }` stanzas — the explicit TMOS declaration.
         `Common` is implicit and never appears here, so we add it unconditionally.
      2. `sys folder /<name>` entries at depth 1 — every partition has a matching
         top-level folder; nested folders like `/DMZ/Drafts` are sub-folders and
         are ignored.
      3. `partition` field that `_orchestrate` tags onto each LTM/GTM object.

    A blind key-walk is **not** used: data-group records (e.g. iRule URI maps like
    `/pfm-main/userPreferences/save` under `ltm data-group internal /Common/uri_dg`)
    legitimately use `/`-prefixed keys that are not partitions.
    """
    partitions: set[str] = {"Common"}

    auth_part = config.get("auth", {}).get("partition", {})
    if isinstance(auth_part, dict):
        for name in auth_part.keys():
            if isinstance(name, str) and name:
                partitions.add(name)

    sys_folder = config.get("sys", {}).get("folder", {})
    if isinstance(sys_folder, dict):
        for path in sys_folder.keys():
            if isinstance(path, str) and path.startswith("/"):
                parts = path.split("/")
                # depth-1 only: "/DMZ" yes, "/DMZ/Drafts" no, "/" no
                if len(parts) == 2 and parts[1]:
                    partitions.add(parts[1])

    for module in ("ltm", "gtm"):
        subtree = config.get(module, {})
        if not isinstance(subtree, dict):
            continue
        for kind, objs in subtree.items():
            if not isinstance(objs, dict):
                continue
            for obj in objs.values():
                if isinstance(obj, dict):
                    p = obj.get("partition")
                    if isinstance(p, str) and p:
                        partitions.add(p)

    return sorted(partitions)


def list_apps(config: dict[str, Any], partition: str | None = None) -> list[str]:
    """Return full paths of every virtual server, optionally filtered by partition."""
    vs_map = config.get("ltm", {}).get("virtual", {})
    paths = [p for p in vs_map.keys() if isinstance(p, str) and p.startswith("/")]
    if partition:
        prefix = f"/{partition}/"
        paths = [p for p in paths if p.startswith(prefix)]
    return sorted(paths)


def app_summary(config: dict[str, Any], partition: str | None = None) -> list[dict[str, Any]]:
    """Lightweight per-app summary for sidebar rendering."""
    out: list[dict[str, Any]] = []
    for path in list_apps(config, partition=partition):
        vs = config["ltm"]["virtual"][path]
        if not isinstance(vs, dict):
            continue
        out.append(
            {
                "name": vs.get("name") or path.rsplit("/", 1)[-1],
                "fullPath": path,
                "partition": vs.get("partition", ""),
                "folder": vs.get("folder"),
                "destination": vs.get("destination", ""),
                "pool": vs.get("pool", ""),
            }
        )
    return out


def _lookup_nested(container: Any, name: str) -> dict[str, Any] | None:
    """Corkscrew's `pathValueFromKey`: scan a nested dict for a key that
    matches `name` at any depth, returning the matched value dict."""
    if not isinstance(container, dict):
        return None
    if name in container and isinstance(container[name], dict):
        return {"path": "", "key": name, "value": container[name]}
    for subkey, sub in container.items():
        if isinstance(sub, dict):
            found = _lookup_nested(sub, name)
            if found is not None:
                found["path"] = subkey if not found["path"] else f"{subkey} {found['path']}"
                return found
    return None


def _fmt_stanza(header: str, body: Any) -> str:
    """Render `header { body }` with the body on its own lines.

    The parser stores `line` as the body joined with `\n` but with no leading or
    trailing newline, so naive f-string concatenation glues the first and last
    body lines to the braces. Normalize here.
    """
    text = body if isinstance(body, str) else ""
    if not text.strip():
        return f"{header} {{}}"
    return f"{header} {{\n{text.rstrip()}\n}}"


def app_details(config: dict[str, Any], full_path: str) -> dict[str, Any] | None:
    """Build a consolidated app view: VS + pool + members + nodes + monitors + profiles + iRules.

    The returned dict always carries a `lines` list with the raw config stanzas
    reconstructed, matching corkscrew's TmosApp shape.
    """
    vs = config.get("ltm", {}).get("virtual", {}).get(full_path)
    if not isinstance(vs, dict):
        return None

    app: dict[str, Any] = {k: v for k, v in vs.items() if k != "line"}
    lines: list[str] = [_fmt_stanza(f"ltm virtual {full_path}", vs.get("line", ""))]

    pool_name = vs.get("pool")
    if isinstance(pool_name, str) and pool_name:
        pool = config.get("ltm", {}).get("pool", {}).get(pool_name)
        if isinstance(pool, dict):
            lines.append(_fmt_stanza(f"ltm pool {pool_name}", pool.get("line", "")))
            pool_copy = {k: v for k, v in pool.items() if k != "line"}

            members = pool_copy.get("members")
            if isinstance(members, dict):
                for member_key, member in members.items():
                    if not isinstance(member, dict):
                        continue
                    member.pop("line", None)
                    name_part = member_key.split(":")[0] if ":" in member_key else member_key
                    node = config.get("ltm", {}).get("node", {}).get(name_part)
                    if isinstance(node, dict):
                        lines.append(_fmt_stanza(f"ltm node {name_part}", node.get("line", "")))

            monitors = pool_copy.get("monitor")
            if monitors:
                mon_list = monitors if isinstance(monitors, list) else [monitors]
                processed: list[Any] = []
                for m in mon_list:
                    if not isinstance(m, str):
                        processed.append(m)
                        continue
                    found = _lookup_nested(config.get("ltm", {}).get("monitor", {}), m)
                    if found is not None:
                        body = dict(found["value"])
                        body.pop("line", None)
                        processed.append(body)
                        lines.append(
                            _fmt_stanza(
                                f"ltm monitor {found['path']} {found['key']}",
                                found["value"].get("line", ""),
                            )
                        )
                pool_copy["monitor"] = processed

            app["pool"] = pool_copy

    profiles = vs.get("profiles")
    if isinstance(profiles, dict):
        profile_names = [k for k in profiles.keys() if k != "line"]
        app["profiles"] = profile_names
        for pname in profile_names:
            found = _lookup_nested(config.get("ltm", {}).get("profile", {}), pname)
            if found is not None:
                lines.append(
                    _fmt_stanza(
                        f"ltm profile {found['path']} {found['key']}",
                        found["value"].get("line", ""),
                    )
                )

    rules = vs.get("rules")
    rule_bodies: dict[str, str] = {}
    if isinstance(rules, list):
        for rule_name in rules:
            rule_body = config.get("ltm", {}).get("rule", {}).get(rule_name)
            if isinstance(rule_body, str):
                rule_bodies[rule_name] = rule_body
                lines.append(f"ltm rule {rule_name} {{\n{rule_body}\n}}")
    app["rule_bodies"] = rule_bodies

    app["lines"] = lines
    return app
