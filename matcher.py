"""Match free-text card titles (and structured player/number pairs) to cards
in a set's checklist.

Titles — whether from an eBay listing or a pasted sold-comp row — are free
text written by sellers, so this is heuristic. A match gets one of three
outcomes:
  - 'exact'  : confident which checklist card it is
  - 'player' : the player is in the checklist but we can't tell which card
  - None     : no player found, or the text names a different year
"""
import re
import unicodedata

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_STOP = {"base", "card", "cards", "the", "and", "set", "of", "series"}
_AUTO = {"auto", "autos", "autograph", "autographs", "autographed", "signature", "signatures", "signed"}
_RELIC = {"relic", "relics", "patch", "jersey", "memorabilia", "swatch", "material", "materials", "rpa"}

_NUM_HASH = re.compile(r"#\s*([A-Za-z]{0,5}-?\d{1,4}[A-Za-z]?)")
_NUM_NO = re.compile(r"\b(?:no\.?|number)\s*([A-Za-z]{0,5}-?\d{1,4}[A-Za-z]?)\b", re.I)
_NUM_PREFIXED = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,5}-\d{1,4})(?![A-Za-z0-9])")
_YEAR = re.compile(r"\b(20[12]\d)\b")


def norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def norm_name(text):
    return " ".join(t for t in norm(text).split() if t not in _SUFFIXES)


def norm_number(text):
    cleaned = re.sub(r"[^A-Z0-9]", "", (text or "").upper())
    return str(int(cleaned)) if cleaned.isdigit() else cleaned


def _title_numbers(title):
    found = set()
    for rx in (_NUM_HASH, _NUM_NO, _NUM_PREFIXED):
        for m in rx.finditer(title or ""):
            found.add(norm_number(m.group(1)))
    return found


class ChecklistMatcher:
    def __init__(self, cards, set_year=None):
        self.set_year = set_year
        self.index = {}
        for c in cards:
            key = norm_name(c["player"])
            if len(key) < 4:
                continue
            sub_tokens = set(norm(c.get("subset") or "").split())
            c = dict(c)
            c["_num"] = norm_number(c.get("card_number") or "")
            c["_sub"] = {t for t in sub_tokens if len(t) > 2 and t not in _STOP}
            c["_auto"] = bool(sub_tokens & _AUTO)
            c["_relic"] = bool(sub_tokens & _RELIC)
            self.index.setdefault(key, []).append(c)

    def _wrong_year(self, title):
        if not self.set_year:
            return False
        years = {int(y) for y in _YEAR.findall(title or "")}
        return bool(years) and not (years & {self.set_year, self.set_year + 1})

    def _score(self, card, tokens, t_auto, t_relic):
        score = len(card["_sub"] & tokens)
        if t_auto:
            score += 3 if card["_auto"] else -2
        elif card["_auto"]:
            score -= 1
        if t_relic:
            score += 3 if card["_relic"] else -2
        elif card["_relic"]:
            score -= 1
        return score

    def match(self, title):
        """Match a free-text title. Return (card_id or None, 'exact' | 'player' | None)."""
        if not title or self._wrong_year(title):
            return None, None

        padded = f" {norm_name(title)} "
        best_key, best_pos = None, None
        for key in self.index:
            pos = padded.find(f" {key} ")
            if pos != -1 and (best_pos is None or pos < best_pos):
                best_key, best_pos = key, pos
        if best_key is None:
            return None, None

        cands = self.index[best_key]
        tokens = set(norm(title).split())
        t_auto, t_relic = bool(tokens & _AUTO), bool(tokens & _RELIC)
        scored = sorted(
            ((self._score(c, tokens, t_auto, t_relic), c) for c in cands),
            key=lambda x: -x[0],
        )

        numbers = _title_numbers(title)
        by_number = [(s, c) for s, c in scored if c["_num"] and c["_num"] in numbers]
        if by_number:
            return by_number[0][1]["id"], "exact"
        if len(cands) == 1:
            return cands[0]["id"], "exact"
        if scored[0][0] >= 2 and scored[0][0] > scored[1][0]:
            return scored[0][1]["id"], "exact"
        return None, "player"

    def match_structured(self, player, card_number=None):
        """Match an explicit player name (plus optional card number) to a checklist
        card — used for CSV rows that have their own Player / Card # columns instead
        of one free-text title. More reliable than match() when the columns exist."""
        if not player:
            return None, None
        key = norm_name(player)
        cands = self.index.get(key)
        if not cands:
            return None, None
        if card_number:
            num = norm_number(card_number)
            direct = [c for c in cands if c["_num"] and c["_num"] == num]
            if direct:
                return direct[0]["id"], "exact"
        if len(cands) == 1:
            return cands[0]["id"], "exact"
        return None, "player"
