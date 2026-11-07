"""finds every translatable text in the code and keeps the catalogs in step with it. used by
scripts/i18n_catalog.py and the tests, never by the running app."""

from __future__ import annotations

import ast
import json
import re
import string
from dataclasses import dataclass
from pathlib import Path

from neonseek import i18n

PACKAGE = Path(__file__).resolve().parent
LOCALES = PACKAGE / "locales"
# %s, %r and %(name)s of argparse texts
_PERCENT = re.compile(r"%(\([A-Za-z_]+\))?[sr]")


@dataclass
class Texts:
    """tr() and N_() texts, ntr() pairs, in the order the code has them."""

    messages: list[str]
    plurals: list[tuple[str, str]]
    # where each text is used, for messages about it
    where: dict[str, str]


def _name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _text(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def extract(package: Path = PACKAGE) -> Texts:
    messages: dict[str, None] = {}
    plurals: dict[tuple[str, str], None] = {}
    where: dict[str, str] = {}
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = sorted(
            (n for n in ast.walk(tree) if isinstance(n, ast.Call)),
            key=lambda n: (n.lineno, n.col_offset),
        )
        for node in calls:
            name = _name(node.func)
            place = f"{path.relative_to(package.parent).as_posix()}:{node.lineno}"
            if name in ("tr", "N_") and node.args and _text(node.args[0]) is not None:
                text = _text(node.args[0])
                messages.setdefault(text)
                where.setdefault(text, place)
            elif name == "ntr" and len(node.args) >= 3:
                one, many = _text(node.args[1]), _text(node.args[2])
                if one is not None and many is not None:
                    plurals.setdefault((one, many))
                    where.setdefault(one, place)
    return Texts(list(messages), list(plurals), where)


def _marked_tables(tree: ast.Module) -> set[str]:
    """module level dicts whose values are all N_("..."), like REASONS = {key: N_(text)}."""
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            values = node.value.values
            if values and all(
                isinstance(v, ast.Call) and _name(v.func) == "N_" and _text(v.args[0]) is not None
                for v in values
            ):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _from_table(node: ast.expr, tables: set[str]) -> bool:
    """TABLE[key] or TABLE.get(key) of a marked table."""
    if isinstance(node, ast.Subscript):
        return isinstance(node.value, ast.Name) and node.value.id in tables
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        owner = node.func.value
        return node.func.attr == "get" and isinstance(owner, ast.Name) and owner.id in tables
    return False


def dynamic_calls(package: Path = PACKAGE) -> list[str]:
    """tr() and ntr() calls whose text is neither written in place nor taken from a table of
    N_() texts, so the catalog would miss it."""
    found = []
    for path in sorted(package.rglob("*.py")):
        if path.name in ("i18n.py", "i18n_catalog.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        tables = _marked_tables(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _name(node.func)
            if name == "tr" and node.args and _text(node.args[0]) is None:
                if _from_table(node.args[0], tables):
                    continue
                found.append(f"{path.name}:{node.lineno} {ast.unparse(node)[:80]}")
            if (
                name == "ntr"
                and len(node.args) >= 3
                and None
                in (
                    _text(node.args[1]),
                    _text(node.args[2]),
                )
            ):
                found.append(f"{path.name}:{node.lineno} {ast.unparse(node)[:80]}")
    return found


def placeholders(text: str) -> list[str]:
    """the fields a text fills in: {count}, {path!r}, %s, %(prog)s. sorted, repeats kept."""
    fields = []
    try:
        for _, name, _spec, conversion in string.Formatter().parse(text):
            if name is not None:
                fields.append("{" + name + ("!" + conversion if conversion else "") + "}")
    except ValueError:
        fields.append("<broken braces>")
    fields += [m.group(0) for m in _PERCENT.finditer(text)]
    return sorted(fields)


def catalog_path(language: str) -> Path:
    return LOCALES / f"{language}.json"


def load_raw(language: str) -> dict:
    path = catalog_path(language)
    if not path.exists():
        return {"language": language, "messages": {}, "plurals": []}
    return json.loads(path.read_text(encoding="utf-8"))


def updated(raw: dict, texts: Texts, language: str) -> tuple[dict, list[str]]:
    """the catalog with every text of the code, in code order, translations kept. new texts get
    an empty translation. returns it and the texts the code no longer has."""
    old_messages = raw.get("messages", {})
    old_plurals = {
        tuple(entry.get(i18n.SOURCE_LANGUAGE, [])): entry.get(language, [])
        for entry in raw.get("plurals", [])
    }
    messages = {text: old_messages.get(text, "") for text in texts.messages}
    plurals = []
    for pair in texts.plurals:
        forms = list(old_plurals.get(pair, []))
        forms += [""] * (i18n.plural_count(language) - len(forms))
        plurals.append({i18n.SOURCE_LANGUAGE: list(pair), language: forms})
    stale = [t for t in old_messages if t not in messages]
    stale += [" | ".join(p) for p in old_plurals if p not in set(texts.plurals)]
    return {"language": language, "messages": messages, "plurals": plurals}, stale


def dump(raw: dict) -> str:
    return json.dumps(raw, ensure_ascii=False, indent=1) + "\n"


def problems(raw: dict, texts: Texts, language: str) -> list[str]:
    """everything that keeps a catalog from being complete and right."""
    found = []
    messages = raw.get("messages", {})
    for text in texts.messages:
        value = messages.get(text, "")
        if not value:
            found.append(f"no translation: {text!r} ({texts.where.get(text)})")
        elif set(placeholders(value)) != set(placeholders(text)):
            # a field may be said once where english says it twice, never left out
            found.append(
                f"fields differ: {text!r} has {placeholders(text)}, the translation "
                f"{placeholders(value)}"
            )
    entries = {
        tuple(e.get(i18n.SOURCE_LANGUAGE, [])): e.get(language, []) for e in raw.get("plurals", [])
    }
    for pair in texts.plurals:
        forms = entries.get(pair, [])
        if len(forms) != i18n.plural_count(language) or not all(forms):
            found.append(f"plural forms missing: {pair[0]!r} ({texts.where.get(pair[0])})")
            continue
        for index, form in enumerate(forms):
            # only the form for one may leave the count out ("jeden plik"), nothing else
            want = set(placeholders(pair[1]))
            have = set(placeholders(form))
            if index == 0:
                want.discard("{count}")
                have.discard("{count}")
            if want != have:
                found.append(f"fields differ: {pair[1]!r} has {want}, the form {form!r} {have}")
    for text in messages:
        if text not in set(texts.messages):
            found.append(f"not used in the code any more: {text!r}")
    for pair in entries:
        if pair not in set(texts.plurals):
            found.append(f"plural not used in the code any more: {pair!r}")
    return found
