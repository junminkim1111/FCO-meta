"""FC 온라인 데이터센터의 선수 검색 폼과 결과 목록."""
from difflib import get_close_matches
from functools import lru_cache
import re
import time
from pathlib import PurePosixPath

import httpx
from selectolax.parser import HTMLParser

from ..market.parser import parse_player_list, parse_player_preview
from ..market.money import format_bp, parse_bp

URL = "https://fconline.nexon.com/datacenter"
FIELDS = {
    "team_color": "teamcolorid", "team_color_category": "strTeamColorCategory",
    "league": "n4LeagueId", "club": "n4TeamId", "continent": "n1Confederation",
    "nationality": "n4NationId", "preferred_foot": "preferredfoot",
    "reputation": "n1InterationalRep",
}
MULTIPLE = {"roles": "strPosition", "seasons": "strSeason", "body_types": "strPhysical"}
NUMBERS = {
    "weak_foot": ("n1FootAblity", 1, 5), "skill_moves": ("n1SkillMove", 1, 6),
    "grade": ("n1Strong", 0, 13), "team_color_bonus": ("n1TeamColor", 0, 9),
    "birth_month": ("n4BirthMonth", 1, 12), "birth_day": ("n4BirthDay", 1, 31),
    "club_year": ("n4PlayYear", 1900, 2100),
}
RANGES = {"salary": "n4Salary", "ovr": "n4Ovr", "birth_year": "n4BirthYear",
          "height": "n4Height", "weight": "n4Weight", "rating": "n4AvgPoint",
          "price": "n8PlayerGrade1"}
TEXT = {"type": "string"}
INTEGER = {"type": "integer"}
STRINGS = {"type": "array", "items": TEXT}
PROPERTIES = {
    "name": {**TEXT, "description": "선수명 (부분 검색)"},
    **{k: TEXT for k in FIELDS},
    "preferred_foot": {**TEXT, "enum": ["왼발", "오른발"]},
    "team_color_category": {**TEXT, "enum": ["소속 팀컬러", "특성 팀컬러"]},
    "roles": {**STRINGS, "description": "FW(ST, CF, LW, RW), MF(CM, CAM, CDM, LM, RM), DF(CB, RB, LB, RWB, LWB), GK"},
    "seasons": STRINGS, "body_types": {**STRINGS, "items": {**TEXT, "enum": ["마름", "보통", "건장"]}},
    **{k: {**INTEGER, "minimum": lo, "maximum": hi} for k, (_, lo, hi) in NUMBERS.items()},
    "adaptation": {**INTEGER, "enum": [1, 5]},
    "club_history": {"type": "boolean", "description": "과거 소속 포함 (기본 true)"},
    "collapse_players": {"type": "boolean", "description": "동일 선수 접기"},
    "traits": {**STRINGS, "maxItems": 3}, "exclude_traits": {**STRINGS, "maxItems": 3},
    "abilities": {"type": "array", "maxItems": 3, "items": {"type": "object",
                 "properties": {"name": TEXT, "min": INTEGER, "max": INTEGER}, "required": ["name"]},
                 "description": "능력치 범위. 예: [{\"name\":\"속력\",\"min\":130}]"},
    "sort": {**TEXT, "description": "급여, OVR, 가격, 평점 또는 능력치 이름"},
    "ascending": {"type": "boolean", "description": "sort 오름차순 (기본 내림차순)"},
}
for key in RANGES:
    for bound in ("min", "max"):
        PROPERTIES[f"{bound}_{key}"] = (
            {**TEXT, "description": "BP, 예: 10억 (데이터센터 단위 1,000 BP)"}
            if key == "price" else {"type": "number" if key == "rating" else "integer"}
        )


def _key(value, field=None):
    key = "".join(str(value).split()).casefold()
    if field == "strSeason":
        return re.sub(r"[-_]", "", key)
    return key


@lru_cache(maxsize=1)
def _form(_day):
    response = httpx.get(URL, timeout=10)
    response.raise_for_status()
    tree = HTMLParser(response.content)
    form = tree.css_first("#form1")
    if not form:
        raise ValueError("데이터센터 검색 폼을 읽을 수 없습니다.")
    defaults = {n.attributes["name"]: n.attributes.get("value", "") for n in form.css("input[name]")
                if n.attributes.get("type") not in ("radio", "checkbox")}
    choices = {}
    labels = {}

    def add(field, label, code, *, display=None):
        if code != "":
            if label and (code != "0" or field == "strPosition"):
                labels.setdefault(field, {}).setdefault(code, display or label)
            options = choices.setdefault(field, {})
            options[_key(label, field)] = options[_key(code, field)] = code

    for a in tree.css("a[onclick]"):
        action = a.attributes["onclick"]
        m = re.search(r"Set(?:AbilitySearch|TeamColorId|TeamColorCategory)\('?([^',)]*)'?,\s*'([^']+)'", action)
        if m:
            add(m[2], a.text(strip=True), m[1])
        elif "SetTeamOption(" in action or "SetNationOption(" in action:
            add("n4LeagueId" if "SetTeamOption(" in action else "n1Confederation",
                a.text(strip=True), a.attributes["data-no"])
    for selector, field in (("position", "strPosition"), ("season", "strSeason"), ("physical", "strPhysical")):
        for n in form.css(f"input.{selector}_check[data-no]"):
            ident = n.attributes["id"]
            code = n.attributes["data-no"]
            label = form.css_first(f'label[for="{ident}"]')
            short = ident.removeprefix(selector + "_").removesuffix("_all")
            display = label.text(strip=True) if selector == "physical" and label else short.upper()
            add(field, short, code, display=display)
            if label and (label.attributes.get("title") or label.text(strip=True) not in ("", "전체")):
                add(field, label.attributes.get("title") or label.text(strip=True), code)
    return defaults, choices, labels


def _search_html(args):
    unknown = args.keys() - PROPERTIES.keys()
    if unknown:
        raise ValueError(f"지원하지 않는 검색 인자: {sorted(unknown)}")
    defaults, choices, labels = _form(int(time.time() // 86400))
    form = dict(defaults, n4PageNo="1")

    def option(field, value, argument):
        try:
            return choices[field][_key(value, field)]
        except KeyError:
            names = labels.get(field, {})
            message = f"{argument}: '{value}' 값을 찾을 수 없습니다."
            if len(names) <= 20 and names:
                message += " 허용값: " + ", ".join(names.values())
            else:
                query = _key(value, field)
                keys = [k for k, code in choices.get(field, {}).items() if code in names and not k.isdecimal()]
                matches = get_close_matches(query, keys, n=len(keys) or 1, cutoff=0.65)
                if len(query) >= 2:
                    matches.extend(k for k in keys if k.startswith(query))
                hints = dict.fromkeys(names[choices[field][k]] for k in matches)
                if hints:
                    message += " 비슷한 후보: " + ", ".join(list(hints)[:3])
            raise ValueError(message) from None

    def items(key, limit=None):
        values = args.get(key, [])
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            raise ValueError(f"{key}: 목록으로 입력하세요.")
        if limit and len(values) > limit:
            raise ValueError(f"{key}: 최대 {limit}개")
        return values

    form["strPlayerName"] = args.get("name", "")
    for key, field in FIELDS.items():
        if key in args:
            form[field] = option(field, args[key], key)
    for key, field in MULTIPLE.items():
        values = [option(field, v, key) for v in items(key)]
        if values:
            form[field] = "," + ",".join(values) + ","
    for key, (field, lo, hi) in NUMBERS.items():
        if key in args:
            value = args[key]
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"{key}: {lo}~{hi} 정수")
            form[field] = str(value)
    for key in ("club_history", "collapse_players", "ascending"):
        if key in args and type(args[key]) is not bool:
            raise ValueError(f"{key}: true/false")
    form["n1History"] = int(not args.get("club_history", True))
    form["IsSummaryPlayer"] = int(args.get("collapse_players", False))
    if args.get("adaptation", 1) not in (1, 5):
        raise ValueError("적응도는 1 또는 5")
    form["n1Grow"] = args.get("adaptation", 1) - 1
    slider_bounds = {"salary": (4, 99), "ovr": (0, 200), "birth_year": (1900, 2010),
                     "height": (140, 250), "weight": (40, 200), "rating": (0, 10),
                     "price": (0, 999900000)}
    for key, field in RANGES.items():
        if f"min_{key}" not in args and f"max_{key}" not in args:
            continue
        form[field + "Min"], form[field + "Max"] = slider_bounds[key]
        for bound in ("min", "max"):
            value = args.get(f"{bound}_{key}")
            if value is None:
                continue
            if key == "price":
                bp = parse_bp(str(value))
                value = (bp + (999 if bound == "min" else 0)) // 1000
            elif type(value) not in ((int, float) if key == "rating" else (int,)):
                raise ValueError(f"{key}: 숫자를 입력하세요")
            form[field + bound.title()] = value
        if form[field + "Min"] > form[field + "Max"]:
            raise ValueError(f"{key}: 최솟값이 최댓값보다 큽니다.")
    for key, field in (("traits", "strTrait"), ("exclude_traits", "strTraitNon")):
        for i, value in enumerate(items(key, 3), 1):
            form[field + str(i)] = option(field + str(i), value, key)
    for i, ability in enumerate(items("abilities", 3), 1):
        if not isinstance(ability, dict) or not ability.get("name") or not ({"min", "max"} & ability.keys()):
            raise ValueError("능력치 이름과 min 또는 max가 필요합니다.")
        form[f"strAbility{i}"] = option("strAbility1", ability["name"], "abilities.name")
        # The site submits both slider handles even when only one was moved.
        form[f"n1Ability{i}Min"], form[f"n1Ability{i}Max"] = 40, 200
        for bound in ("min", "max"):
            if bound in ability:
                if type(ability[bound]) is not int:
                    raise ValueError("능력치 범위는 정수입니다.")
                form[f"n1Ability{i}{bound.title()}"] = ability[bound]
        if form[f"n1Ability{i}Min"] > form[f"n1Ability{i}Max"]:
            raise ValueError("능력치 최솟값이 최댓값보다 큽니다.")
    if "sort" in args:
        sort = {"급여": "salary", "ovr": "overallrating", "가격": "n8playergrade1",
                "평점": "n4AvgAssessmentPoint"}.get(_key(args["sort"]))
        if not sort:
            sort = option("strSkill1", args["sort"], "sort")
            form["strSkill1"] = sort
        form["strOrderby"] = " " + sort + (" " if args.get("ascending") else " descending")

    response = httpx.post(URL + "/PlayerList", data=form, timeout=15)
    response.raise_for_status()
    return response.text


def search_players(**args):
    tree = HTMLParser(_search_html(args))
    players = []
    for row in tree.css('div[id^="area_playerunit_"]'):
        season = row.css_first(".info_top .season img")
        name = row.css_first(".info_top .name")
        if not season or not name:
            raise ValueError("데이터센터 선수 카드 형식이 변경되었습니다.")
        card = {"season": PurePosixPath(season.attributes["src"]).stem, "name": name.text(strip=True)}
        card.update(price_bp=None, price=None)
        if "grade" in args:
            card["grade"] = args["grade"]
            price = row.css_first(f".td_ar_bp .span_bp{card['grade']}")
            if price:
                amount = (price.attributes.get("title") or price.attributes.get("alt") or "").replace(",", "")
                if amount.isdecimal():
                    card["price_bp"] = int(amount)
                    card["price"] = format_bp(card["price_bp"])
        players.append(card)
    if not players and not tree.css_first(".tr.empty"):
        raise ValueError("데이터센터 검색 결과를 읽을 수 없습니다.")
    return {"players": players, "possibly_truncated": len(players) >= 200}

def season_cards(name, season):
    html = _search_html({"name": name, "seasons": [season]})
    cards = parse_player_list(html)
    if not cards and not HTMLParser(html).css_first(".tr.empty"):
        raise ValueError("데이터센터 검색 결과를 읽을 수 없습니다.")
    return cards


def card_preview(spid):
    response = httpx.post(URL + "/PlayerPreView", data={"spid": spid, "n1strong": 1,
                          "n1Grow": 0, "n1TeamColor": 0}, timeout=15)
    response.raise_for_status()
    return parse_player_preview(response.text, spid, 1)
