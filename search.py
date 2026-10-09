"""Умный поиск по каталогу: без внешних сервисов, работает и на SQLite, и на PostgreSQL.

Что умеет:
  • синонимы и разные написания: «камри» = camry = кэмри, «мерс» = mercedes, «гелик» = g-class;
  • падежи и окончания: «свадьбу» найдёт «свадьбы», «белый» найдёт «белая»;
  • поиск не только по марке и модели, но и по описанию, условиям и городу;
  • слова-свойства: «свадьба», «с водителем», «доставка», «автомат», «премиум» учитывают и галочки объявления;
  • лишние слова («машина на свадьбу») не мешают;
  • все слова запроса должны найтись (И), а варианты одного слова — (ИЛИ).
"""
import re
from collections import defaultdict

from config import SEARCH_FAMILIES, SEARCH_FLAGS, SEARCH_STOPWORDS, SEARCH_SYNONYMS


def _as_list(value) -> list[str]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _build_family_index() -> dict[str, list[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for key, value in SEARCH_SYNONYMS.items():
        for item in _as_list(value):
            union(key.lower(), item.lower())
    for family in SEARCH_FAMILIES:
        for member in family[1:]:
            union(family[0].lower(), member.lower())

    groups: dict[str, set[str]] = defaultdict(set)
    for member in list(parent):
        groups[find(member)].add(member)
    index: dict[str, list[str]] = {}
    for members in groups.values():
        ordered = sorted(members)
        for member in members:
            index[member] = ordered
    return index


FAMILY_INDEX = _build_family_index()
_PHRASES = sorted((m for m in FAMILY_INDEX if " " in m), key=len, reverse=True)

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ы": "y", "э": "e",
    "ю": "yu", "я": "ya", "ь": "", "ъ": "",
}


_SUFFIXES = sorted(
    ["ого", "его", "ому", "ему", "ыми", "ими", "ый", "ий", "ой", "ая", "яя", "ое", "ее", "ую", "юю", "ым", "им",
     "ом", "ем", "ей", "ов", "ам", "ям", "ах", "ях", "ами", "ями", "у", "ю", "а", "я", "ы", "и", "е", "о", "ь", "й"],
    key=len, reverse=True,
)


def stem(word: str) -> str:
    """Грубо отрезает окончание: «свадьбу» -> «свадьб», «грозного» -> «грозн», «белый» -> «бел».
    Короткие слова и латиницу не трогаем."""
    if len(word) < 5 or not re.search("[а-яё]", word):
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _translit(word: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in word)


def _family_for(token: str) -> list[str] | None:
    if token in FAMILY_INDEX:
        return FAMILY_INDEX[token]
    token_stem = stem(token)
    if len(token_stem) >= 4:
        for member, family in FAMILY_INDEX.items():
            if len(member) >= 4 and stem(member) == token_stem:
                return family
    return None


def parse_search(raw: str) -> list[dict]:
    """Разбирает запрос на условия. Каждое условие:
      variants — варианты написания слова (ищем любой из них в тексте объявления);
      flag     — имя булева поля объявления (например for_wedding), если слово описывает свойство;
      flag_value — нужное значение поля (False для «без водителя»);
      year     — год, если слово похоже на год выпуска."""
    text = re.sub(r"[^\w\s\-]", " ", (raw or "").lower().replace("ё", "е"))
    text = re.sub(r"\s+", " ", text).strip()[:80]
    if not text:
        return []

    conditions: list[dict] = []
    for phrase in _PHRASES:
        if phrase in text:
            conditions.append({"variants": list(FAMILY_INDEX[phrase])})
            text = text.replace(phrase, " ")

    negate_next = False
    for token in text.split():
        if token == "без":
            negate_next = True
            continue
        negated, negate_next = negate_next, False
        if token in SEARCH_STOPWORDS:
            continue

        flag = next((name for prefix, name in SEARCH_FLAGS.items() if token.startswith(prefix.replace("ё", "е"))), None)
        if flag:
            conditions.append({
                "variants": [stem(token)], "flag": flag,
                "flag_value": not negated if flag == "with_driver" else True,
                "negated": negated and flag == "with_driver",
            })
            continue

        if re.fullmatch(r"(19|20)\d{2}", token):
            conditions.append({"variants": [token], "year": int(token)})
            continue

        family = _family_for(token)
        variants = list(family) if family else [stem(token)]
        if re.search("[а-яё]", token) and not family:
            variants.append(_translit(token))
        conditions.append({"variants": list(dict.fromkeys(v for v in variants if v))})
    return conditions[:6]
