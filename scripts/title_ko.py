"""영문 기사 제목 → 자연스러운 한국어 제목 (기계번역 전처리·후처리)

무료 번역기(구글/MyMemory)의 직역투를 줄이기 위한 규칙 모음.
 - 전처리: 오역이 잦은 관용구를 쉬운 영어로 바꾸고, 회사·브랜드명과 금액은 미리 한국어로 치환
 - 후처리: K뷰티 등 표기 통일, 금액·기호 정리, 존댓말(~습니다) → 기사 제목체(~한다), 끝 마침표 제거
번역기 원본은 title_ko_raw 로 저장되어, 후처리(post) 규칙만 바꾸면 다음 수집 때 번역 API 호출 없이 재적용됨.
전처리(pre)·관용구 규칙을 바꿨을 때만 VERSION 을 올릴 것 → 기존 번역도 다시 번역됨(무료 한도 소모).
"""
from __future__ import annotations

import re

VERSION = 3

# 회사·브랜드·고유명사 (영문 → 국내 언론 표기). 긴 것부터 치환.
NAMES = {
    "Korea Kolmar": "한국콜마", "Kolmar Korea": "한국콜마", "Cosmecca Korea": "코스메카코리아", "Cosmax": "코스맥스",
    "Amorepacific": "아모레퍼시픽", "AmorePacific": "아모레퍼시픽", "LG Household & Health Care": "LG생활건강",
    "LG H&H": "LG생활건강", "Medicube": "메디큐브", "Olive Young": "올리브영", "d'Alba": "달바", "Hugel": "휴젤",
    "Medytox": "메디톡스", "PharmaResearch": "파마리서치", "Pharma Research": "파마리서치", "Rejuran": "리쥬란",
    "Classys": "클래시스", "Daewoong": "대웅제약", "MUSINSA": "무신사", "Musinsa": "무신사", "Rakuten": "라쿠텐",
    "Hallyu": "한류", "KOTRA": "코트라", "Korea Trade Body": "코트라", "Ulta Beauty": "얼타뷰티", "Sephora": "세포라",
    "Costco": "코스트코", "Walmart": "월마트", "TikTok Shop": "틱톡샵", "Amazon Prime Day": "아마존 프라임데이",
    "Prime Day": "프라임데이", "Amazon": "아마존", "Anua": "아누아", "Beauty of Joseon": "조선미녀", "COSRX": "코스알엑스",
    "Torriden": "토리든", "Skin1004": "스킨1004", "SKIN1004": "스킨1004", "Round Lab": "라운드랩", "VT Cosmetics": "VT코스메틱",
}
# 번역기가 자주 틀리는 한국어 표기 → 국내 언론 표기
KO_FIX = {
    "콜마코리아": "한국콜마", "코리아 콜마": "한국콜마", "올리브 영": "올리브영", "프라임 데이": "프라임데이",
    "아모레 퍼시픽": "아모레퍼시픽", "메디 큐브": "메디큐브", "틱톡 샵": "틱톡샵", "시프트": "전환",
    "플레이북": "전략", "피벗": "전환",
}
# APR 은 'April' 과 헷갈리므로 대문자 단어일 때만
APR = re.compile(r"\bAPR(?='s\b|\b)")

# 오역이 잦은 관용구 → 번역기가 제대로 옮기는 쉬운 영어
IDIOMS = [
    (r"\bLos(?:es|ing|t) Ground\b", "Loses Share"),
    (r"\bGain(?:s|ing|ed)? Ground\b", "Gains Share"),
    (r"\bPosts? Record (Sales|Revenue|Profit|Results)\b", r"Achieves All-Time High \1"),
    (r"\bPosts? Record\b", "Achieves All-Time High"),
    (r"\bEyes (IPO|Listing|Expansion|Entry)\b", r"Pursues \1"),
    (r"\bSets? Sights on\b", "Targets"),
    (r"\bTops (?=\$|\d)", "Exceeds "),
    (r"\bTop (?=\$|\d)", "Exceed "),
    (r"\b(Market|Category|Demand)\s+Contracts\b", r"\1 Shrinks"),
    (r"\b(Markets|Categories|Sales|Exports|Imports)\s+Contract\b", r"\1 Shrink"),
    (r"\bSlumps?\b", "Declines"),
    (r"\bPlaybook\b", "Strategy"),
    (r"\bPivot\b", "Shift"),
    (r"\bStaple\b", "Essential"),
    (r"\bValuation Buzz\b", "Valuation Expectations"),
    (r"\bTakes ([\w-]+) Push to\b", r"Expands \1 to"),
    (r"\b([\w-]+) Push\b", r"\1 Initiative"),
    (r"\bGoes Mainstream\b", "Becomes Mainstream"),
    (r"\bGoes Premium\b", "Becomes Premium"),
    (r"\bYoY\b", "Year-on-Year"),
    (r"\bGen Z\b", "Generation Z"),
]


def _money(num: str, unit: str) -> str:
    x = float(num.replace(",", ""))
    u = unit.lower()
    if u in ("billion", "bn", "b"):
        eok = x * 10
    elif u in ("million", "mn", "m"):
        if x < 100:
            return f"{x * 100:g}만 달러"
        eok = x / 100
    else:
        return f"{num}{unit} 달러"
    return f"{eok:,.1f}".rstrip("0").rstrip(".") + "억 달러"


MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?(Billion|billion|Bn|bn|B|Million|million|Mn|mn|M)\b")
QUARTER = re.compile(r"\bQ([1-4])\s+(20\d\d)\b")


def pre(title: str) -> str:
    """번역기에 넣기 전: 기호 정리 + 오역 잦은 관용구를 쉬운 영어로. (한국어를 섞으면 번역기가 헷갈리므로 영어로만)"""
    s = title.replace("\ufeff", "").replace("\u200b", "")
    s = re.sub(r"[′’‘`]", "'", s).replace("“", '"').replace("”", '"')
    s = re.sub(r"\s+", " ", s).strip()
    for pat, rep in IDIOMS:
        s = re.sub(pat, rep, s)
    s = APR.sub("APR Corp", s)  # 'APR' 을 4월(April)로 옮기는 것 방지
    return s


K_WORDS = {"fashion": "패션", "pop": "팝", "food": "푸드", "culture": "컬처", "content": "콘텐츠",
           "drama": "드라마", "botox": "보톡스"}

# ---------- 후처리
_BASE, _JONG_B, _JONG_N, _JONG_SS = 0xAC00, 17, 4, 20


def _jong(ch: str) -> int:
    o = ord(ch) - _BASE
    return o % 28 if 0 <= o < 11172 else -1


def _set_jong(ch: str, j: int) -> str:
    o = ord(ch) - _BASE
    return chr(_BASE + o - o % 28 + j)


def _plain(m: re.Match) -> str:
    """존댓말 종결 → 해라체. (찾습니다→찾는다, 커집니다→커진다, 있습니다→있다, 했습니다→했다)"""
    stem, end = m.group(1), m.group(2)
    if end == "니다":  # stem 마지막 글자에 받침 ㅂ (합니다, 됩니다, 커집니다)
        return stem[:-1] + _set_jong(stem[-1], _JONG_N) + "다"
    last = stem[-1]
    if last in "있없" or _jong(last) == _JONG_SS:
        return stem + "다"
    return stem + "는다"


# 받침 유무에 따른 조사 짝 (받침 없음, 받침 있음)
PARTICLES = [("가", "이"), ("는", "은"), ("를", "을"), ("와", "과"), ("로", "으로")]


def replace_word(s: str, wrong: str, right: str) -> str:
    """단어를 바꾸면서 바로 뒤 조사를 새 단어의 받침에 맞춤. (시프트가 → 전환이)"""
    j = _jong(right[-1])
    has_final = j > 0

    def fix(m: re.Match) -> str:
        part = m.group(1) or ""
        for no, yes in PARTICLES:
            if part in (no, yes):
                if no == "로":  # ㄹ 받침 뒤는 '로'
                    part = "으로" if has_final and j != 8 else "로"
                else:
                    part = yes if has_final else no
                break
        return right + part

    return re.sub(re.escape(wrong) + r"(으로|이|가|은|는|을|를|과|와|로)?(?![가-힣])", fix, s)


POLITE_SEUB = re.compile(r"([가-힣]+)(습)니다")


def post(ko: str) -> str:
    s = ko.replace("\ufeff", "").replace("\u200b", "")
    s = re.sub(r"[′’‘`]", "'", s)
    # K뷰티 계열 표기 통일
    s = re.sub(r"([KkCcJj])\s?-\s?(?:뷰티|Beauty|BEAUTY|beauty)", lambda m: m.group(1).upper() + "뷰티", s)
    s = re.sub(r"([Kk])\s?-\s?(패션|팝|푸드|컬처|보톡스|드라마|콘텐츠)", lambda m: "K" + m.group(2), s)
    s = re.sub(r"\b[Kk]\s?-\s?(Fashion|Pop|POP|Food|Culture|Content|Drama|Botox)(?![A-Za-z])",
               lambda m: "K" + K_WORDS[m.group(1).lower()], s)
    # 회사·브랜드명: 번역 후 남은 영문 + 번역기가 틀리게 옮긴 표기
    s = re.sub(r"APR\s?(?:Corp\.?|코퍼레이션|코프|코퍼레이트|주식회사)?(?:'s)?", "에이피알", s)
    for en in sorted(NAMES, key=len, reverse=True):
        s = re.sub(r"(?<![A-Za-z])" + re.escape(en) + r"(?:'s)?(?![A-Za-z])", NAMES[en], s)
    for wrong, right in KO_FIX.items():
        s = replace_word(s, wrong, right)
    s = QUARTER.sub(lambda m: f"{m.group(2)}년 {m.group(1)}분기", s)
    s = re.sub(r"\b(20\d\d)년?\s?Q([1-4])\b", r"\1년 \2분기", s)
    # 남은 금액 표기
    s = MONEY.sub(lambda m: _money(m.group(1), m.group(2)), s)
    s = re.sub(r"(\d[\d.,]*\s?[억만천]?)\s?\$", r"\1 달러", s)
    s = re.sub(r"\$\s?(\d[\d.,]*)\s?(억|만)", r"\1\2 달러", s)
    # 존댓말 → 제목체
    s = s.replace("아닙니다", "아니다").replace("입니다", "이다")
    s = POLITE_SEUB.sub(_plain, s)
    s = re.sub(r"([가-힣])니다", lambda m: (_set_jong(m.group(1), _JONG_N) + "다") if _jong(m.group(1)) == _JONG_B else m.group(0), s)
    s = re.sub(r"(인가|나|까|는가)요\?", r"\1?", s)
    # 기호·공백 정리
    s = re.sub(r"\[\s*([^\]]*?)\s*\]", lambda m: "[" + m.group(1).strip() + "]", s)
    s = re.sub(r"'\s+([^']*?)\s+'", r"'\1'", s)
    s = re.sub(r"\s+([,.:;!?…])", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    s = re.sub(r"(?<![.])\.$", "", s)  # 끝 마침표 제거 (말줄임표는 유지)
    return s
