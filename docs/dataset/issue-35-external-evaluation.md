# 이슈 #35 외부 데이터 일반화 평가

평가일: 2026-11-02

이슈: <https://github.com/ProDog-2026/workspace/issues/35>
구현 PR: <https://github.com/ProDog-2026/workspace/pull/48>

대상 커밋: `97e3fd5` 및 이 평가 코드 변경

목적: TF 계열과 #31 Transformer를 결합한 운영 탐지기가 외부 데이터에서 어떻게 동작하는지
측정한다. 이슈 완료 조건에 따라 #31 적용 전후 비교도 함께 기록한다.

## 고정 조건

평가 결과를 보고 임계값이나 가중치를 조정하지 않았다. 이번 데이터를 조정에 사용하면 더 이상
외부 테스트로 볼 수 없기 때문이다.

| 모델 | suspicious | danger |
| --- | ---: | ---: |
| TF 계열 | 0.05 | 0.414419 |
| Transformer | 0.939322 | 0.942692 |

결합 판정은 두 모델의 확률을 직접 비교하지 않는다. 각 구성요소가 자기 임계값으로 내린
`safe < suspicious < danger` 단계 중 더 높은 단계를 최종 결과로 사용한다.

## 평가 데이터

### InjecAgent Gmail

- 출처: <https://github.com/uiuc-kang-lab/InjecAgent>
- 사용 파일: `test_cases_dh_base.json`, `test_cases_ds_base.json`
- GmailReadEmail 62건, GmailSearchEmails 62건
- 공격 사례만 124건이며 정상 사례는 없다.
- TF 계열·Transformer 학습, 검증, 임계값 선택에서 제외했다.

### KoPI-Bench 한국어 메일 후보

- 출처: <https://huggingface.co/datasets/HaniMeni/KoPI-Bench>
- 사용 파일: `docs/dataset/KoPI-Bench_이메일_공격위치.csv`
- 공격 1,572건, 정상 1,234건, 합계 2,806건
- 영어 공개 자료를 한국어로 번역한 텍스트이며 실제 국내 메일이나 `.eml` 원본은 아니다.
- 메일 후보 자동 선별과 공격 위치 추정에 오류가 있을 수 있고 Mercury 계열 템플릿 비중이 높다.
- 현재 두 모델의 학습·임계값 선택에는 사용하지 않았지만, 원 자료 계보가 기존 공개 벤치마크와
  겹칠 가능성이 있어 독립 외부 성능보다 한국어 동작 확인용 보조 평가로 해석한다.

## 결과

### 결합 탐지 로직의 최종 결과

운영 탐지기는 TF 계열과 Transformer 중 더 높은 판정을 선택하는 하나의 로직이다. 따라서
아래 결합 최종 판정이 실제 성능이다.

| 평가셋·정답 | safe | suspicious | danger | 검토 게이트 결과 |
| --- | ---: | ---: | ---: | --- |
| InjecAgent 공격 124건 | 0 | 47 | 77 | 공격 재현율 100%, 미탐률 0% |
| KoPI-Bench 공격 1,572건 | 0 | 1,026 | 546 | 공격 재현율 100%, 미탐률 0% |
| KoPI-Bench 정상 1,234건 | 0 | 818 | 416 | 정상 오탐률 100% |

결합 탐지는 두 평가셋의 공격을 모두 `suspicious` 이상으로 잡았다. 그러나 한국어 정상 메일도
전부 잡았으므로 KoPI-Bench에서는 정상과 공격을 구분하지 못했다. `danger`만 탐지로 보면
KoPI-Bench 공격 재현율은 34.73%, 정상 오탐률은 33.71%다.

InjecAgent에는 정상 사례가 없어 결합 탐지의 정상 오탐률을 계산할 수 없다. 이전에 기록한
미탐률 15.3%(19/124)는 `suspicious=0.338996`일 때의 결과다. 현재 운영 경계 `0.05` 결과와
모델 개선 전후 수치로 직접 비교하지 않는다.

### #31 적용 전후 비교용 진단

이슈 완료 조건에 따라 같은 평가셋에서 TF 계열 단독과 결합 탐지를 비교했다. 결합 후 판정이
바뀐 사례는 InjecAgent와 KoPI-Bench 모두 0건이었다. Transformer 단독 출력은 두 평가셋을
모두 `safe`로 분류했다. 이 수치는 배포된 별도 탐지기의 성능이 아니라, 이번 표본에서 최종
결합 판정이 전부 TF 계열 쪽에서 결정된 원인을 확인하기 위한 진단이다.

## 결론

1. 결합 탐지는 평가 공격을 모두 검토 대상으로 올려 공격 미탐률 0%를 기록했다.
2. 동시에 KoPI-Bench 정상도 전부 검토 대상으로 올려 정상 오탐률 100%를 기록했다. 따라서
   현재 결합 로직은 한국어 메일에서 정상과 공격을 구분하지 못한다.
3. InjecAgent에는 정상 사례가 없어 결합 탐지의 영어 외부 오탐률을 판단할 수 없다. 영어 정상
   메일을 외부에서 추가로 구하지는 않았다. 공개 영어 ham 말뭉치(Enron, SpamAssassin,
   TREC_05/06/07)는 이미 phishing-curated 학습 풀에 포함되어 있어 같은 계보를 외부셋이라고
   부를 수 없기 때문이다. 영어 정상 오탐의 근거는 그룹 분리된 phishing-curated test ham
   33,761건(오탐 0.25%)으로 유지하고, 측정이 필요한 쪽은 한국어 정상으로 본다.
4. #31 Transformer가 이번 두 평가셋의 최종 판정을 바꾸지는 않았지만, 운영 결과는 항상 두
   구성요소를 합친 하나의 최종 판정으로 해석한다.
5. 운영 임계값은 이 평가에서 다시 맞추지 않았다. 결과를 사용해 조정하려면 KoPI-Bench를
   개발셋으로 재분류하고, 별도의 미사용 최종 테스트셋을 마련해야 한다.

## 대시보드 프론트엔드에 남은 작업

이슈 완료 조건의 `static_score` 출처는 탐지 모델로 확정했다. 그런데 현재 대시보드
프론트엔드는 `src/dashboard/src/rules.js`의 데모 규칙 R01~R07로 근거를 표시한다. 이 규칙은
흰 글씨, `display:none` 계열 CSS 숨김, HTML 주석 안 명령문, 제로폭 문자, 지시 무시 문구,
외부 전송 링크, Base64 은닉 같은 **은닉 기법**을 정규식으로 찾는다.

정적 탐지기는 이 유형을 산출하지 않는다. 출력은 인젝션 확률 하나와 임계값으로 파생한
`safe`/`suspicious`/`danger` 3단계뿐이고, 어떤 문장이나 어떤 기법 때문에 그 판정이 나왔는지는
반환하지 않는다. 따라서 R01~R07은 모델 판정의 근거가 아니며, 두 경로는 현재 연결되어 있지
않다.

후속 작업으로 다음을 남긴다.

1. 결과 화면의 판정과 점수는 모델의 verdict와 `static_score`로 표시한다. 실제 Gmail 대시보드는
   이미 모델 verdict를 사용하므로 대상은 데모 화면과 목업이다.
2. R01~R07 기반 근거 표시는 제거한다. 모델이 근거 문장을 제공하지 않으므로 상세 근거 영역은
   비게 된다. 모델이 탐지 유형과 문장을 함께 반환하도록 확장하는 일은 별도 이슈로 분리한다.
3. 분리 전까지 R01~R07 화면을 남겨두는 경우, 데모용 규칙이고 실제 판정 근거가 아님을 화면에
   명시한다. 사용자가 데모 규칙을 모델의 판정 근거로 오해할 수 있기 때문이다.

## 한국어 데이터 다음 단계

1. **KoPI-Bench를 개발용으로 전환한다.** 원본의 출처·유형 열을 보존해 같은 템플릿과 원문
   계보가 train·validation·test를 넘지 않도록 그룹을 만든다. 현재 선별 CSV는 출처 열이 없어
   재분할 근거로 부족하다.
2. **한국어 정상 hard negative를 먼저 보강한다.** 결제, 청구, 영수증, 보안 안내, 업무 요청처럼
   명령형 표현이 있지만 인젝션은 아닌 메일이 필요하다. 실제 사용자 메일은 저장하지 않고
   공개·합성·익명화 자료만 사용한다.
3. **한국어 공격은 정상 메일과 쌍으로 만든다.** 동일한 정상 본문에 공격 문장만 삽입한 paired
   형식으로 구성하면 템플릿 자체가 아니라 삽입 문구의 영향을 측정할 수 있다.
4. **Transformer 기반 모델을 한국어 또는 다국어 인코더로 교체해 비교한다.** 현재 영어
   BERT-tiny의 임계값을 한국어에 그대로 적용하지 않는다. 후보 비교는 동일 그룹 분할과 동일
   오탐 예산에서 진행한다.
5. **최종 한국어 테스트셋은 끝까지 보존한다.** 개발용 KoPI-Bench와 별도로 실제 서비스 형식의
   합성 `.eml` 정상·공격 쌍을 만들고, 모델과 임계값을 확정한 뒤 한 번만 평가한다. 규모와 수용
   기준은 한국어 데이터 후속 이슈에서 확정한다.

## 재현

```powershell
cd src/detector
python scripts/evaluate_injecagent.py `
  data/vendor/InjecAgent/data `
  --model models/prompt_injection.joblib `
  --transformer-model models/quadrat-transformer `
  --output reports/injecagent-gmail-metrics.json

python scripts/evaluate_dataset.py `
  ../../docs/dataset/KoPI-Bench_이메일_공격위치.csv `
  --model models/prompt_injection.joblib `
  --transformer-model models/quadrat-transformer `
  --output reports/kopi-bench-email-metrics.json
```

전체 기계 판독 결과와 입력·가중치 SHA-256은 다음 파일에 기록한다.

- `src/detector/reports/injecagent-gmail-metrics.json`
- `src/detector/reports/kopi-bench-email-metrics.json`
