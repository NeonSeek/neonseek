"""keeps the translation catalogs in step with the code (D-163).

uv run python scripts/i18n_catalog.py           adds new texts of the code with an empty
                                                translation, drops texts the code no longer has
uv run python scripts/i18n_catalog.py --check   changes nothing, lists what is missing or wrong
"""

from __future__ import annotations

import argparse

from neonseek import i18n, i18n_catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="only list the problems")
    args = parser.parse_args()
    texts = i18n_catalog.extract()
    dynamic = i18n_catalog.dynamic_calls()
    for line in dynamic:
        print(f"text not written in place, mark it with N_() where it is defined: {line}")
    failed = bool(dynamic)
    for language in i18n.LANGUAGES:
        if language == i18n.SOURCE_LANGUAGE:
            continue
        raw = i18n_catalog.load_raw(language)
        if not args.check:
            raw, stale = i18n_catalog.updated(raw, texts, language)
            i18n_catalog.catalog_path(language).write_text(
                i18n_catalog.dump(raw), encoding="utf-8", newline="\n"
            )
            for text in stale:
                print(f"{language}: dropped, the code no longer has it: {text!r}")
        found = i18n_catalog.problems(raw, texts, language)
        for line in found:
            print(f"{language}: {line}")
        print(
            f"{language}: {len(texts.messages)} texts, {len(texts.plurals)} plurals, "
            f"{len(found)} problems"
        )
        failed = failed or bool(found)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
