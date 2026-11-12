from prodog_sast.email_text import fields_to_model_text
from prodog_sast.language import hangul_ratio, is_korean

KOREAN = "제목: 회의 일정 안내\n안녕하세요. 11월 20일 오후 3시에 회의를 진행합니다. 감사합니다."
ENGLISH = "Subject: Invoice attached\nHi David, please find the October invoice attached."


def test_routes_korean_and_english_mail() -> None:
    assert is_korean(KOREAN)
    assert not is_korean(ENGLISH)


def test_routes_the_serving_representation_the_same_way() -> None:
    # CLI가 붙이는 [SUBJECT]/[PLAIN] 표식은 영어라 비율을 내리지만 판정은 그대로여야 한다.
    assert is_korean(fields_to_model_text(body_text=KOREAN))
    assert not is_korean(fields_to_model_text(body_text=ENGLISH))


def test_korean_body_with_english_signature_stays_korean() -> None:
    assert is_korean(KOREAN + "\nBest regards,\nJamie Park\nMoonchaser Inc.")


def test_ignores_digits_and_punctuation() -> None:
    # 금액·계좌번호가 많은 거래 알림 메일이 비율을 끌어내리지 않아야 한다.
    assert is_korean("제목: 청구 안내\n2026-10-31 / $1,234.56 / ••4605 / 42,310원 청구되었습니다.")


def test_text_without_letters_is_not_korean() -> None:
    assert hangul_ratio("1234 ... !!!") == 0.0
    assert not is_korean("")
