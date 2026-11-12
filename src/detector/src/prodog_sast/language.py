"""Pick the detector language for a mail by counting Hangul letters.

Measured on the KoPI email corpus and the Korean smoke set: Korean mail sits at
0.465 or above, English mail at 0.000, and Korean mail with an English signature
around 0.273. No language-id dependency earns its place against that gap.
"""

from __future__ import annotations

import re

HANGUL = re.compile(r"[가-힣]")
# 숫자·구두점을 세면 금액과 계좌번호가 많은 거래 알림 메일이 비율을 왜곡한다.
LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
KOREAN_RATIO = 0.2


def hangul_ratio(text: str) -> float:
    letters = LETTER.findall(text)
    return len(HANGUL.findall(text)) / len(letters) if letters else 0.0


def is_korean(text: str, ratio: float = KOREAN_RATIO) -> bool:
    """Whether the mail should go to the Korean detectors.

    ponytail: 메일 전체를 한 언어로 본다. 한국어 메일에 끼워넣은 영어 주입 문장은
    한국어 모델이 영어 공격을 배운 적이 없어 놓칠 수 있다. 막으려면 언어가 바뀌는
    지점에서 끊어 구간별로 판정해야 한다. 짧은 영어 서명이 영어 모델의 오탐을
    되살리므로 그때는 구간 최소 길이 보정이 함께 필요하다.
    """
    return hangul_ratio(text) >= ratio
