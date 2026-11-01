# 이슈 #35 외부 데이터 일반화 평가

평가일: 2026-11-02

대상 커밋: `97e3fd5` 및 이 평가 코드 변경

목적: TF 계열 정적 탐지기와 #31 Transformer 병렬 탐지를 같은 외부 데이터에서 비교한다.

## 고정 조건

평가 결과를 보고 임계값이나 가중치를 조정하지 않았다. 이번 데이터를 조정에 사용하면 더 이상
외부 테스트로 볼 수 없기 때문이다.

| 모델 | suspicious | danger |
| --- | ---: | ---: |
| TF 계열 | 0.05 | 0.414419 |
| Transformer | 0.939322 | 0.942692 |

병렬 판정은 두 모델의 확률을 직접 비교하지 않는다. 각 모델이 자기 임계값으로 내린
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

### InjecAgent Gmail 124건

| 구성 | safe | suspicious | danger | 공격 미탐률 | 정상 오탐률 |
| --- | ---: | ---: | ---: | ---: | --- |
| TF 계열 단독 | 0 | 47 | 77 | 0% | 계산 불가 |
| Transformer 단독 | 124 | 0 | 0 | 100% | 계산 불가 |
| 병렬 최종 판정 | 0 | 47 | 77 | 0% | 계산 불가 |

병렬 모델이 TF 계열 단독 판정을 바꾼 사례는 0건이다. 이전에 기록한 미탐률 15.3%(19/124)는
`suspicious=0.338996`일 때의 결과다. 현재 운영 경계 `0.05`로 낮춘 효과가 포함되므로 모델 개선
전후 수치로 직접 비교하지 않는다.

### KoPI-Bench 한국어 메일 후보 2,806건

#### suspicious 이상을 탐지로 보는 검토 게이트

| 구성 | 공격 재현율 | 공격 미탐률 | 정상 오탐률 | 정밀도 |
| --- | ---: | ---: | ---: | ---: |
| TF 계열 단독 | 100% | 0% | 100% | 56.02% |
| Transformer 단독 | 0% | 100% | 0% | 0% |
| 병렬 최종 판정 | 100% | 0% | 100% | 56.02% |

#### 3단계 분포

| 구성·레이블 | safe | suspicious | danger |
| --- | ---: | ---: | ---: |
| TF 단독·정상 | 0 | 818 | 416 |
| TF 단독·공격 | 0 | 1,026 | 546 |
| Transformer 단독·정상 | 1,234 | 0 | 0 |
| Transformer 단독·공격 | 1,572 | 0 | 0 |
| 병렬·정상 | 0 | 818 | 416 |
| 병렬·공격 | 0 | 1,026 | 546 |

`danger`만 탐지로 보면 병렬 모델의 공격 재현율은 34.73%, 정상 오탐률은 33.71%다. 병렬
모델이 TF 계열 단독 판정을 바꾼 사례는 여기서도 0건이다.

## 결론

1. InjecAgent 공격 미탐률 0%만으로 일반화가 좋다고 주장할 수 없다. 정상 사례가 없어 외부
   오탐률을 함께 측정하지 못했다.
2. TF 계열의 `suspicious=0.05`는 KoPI-Bench에서 정상과 공격을 구분하지 못하고 전부 검토
   대상으로 올렸다. 한국어 메일의 `safe` 통과 경계로 사용할 수 없다.
3. 영어 Quadrat-IPI로 학습한 현재 Transformer는 InjecAgent와 KoPI-Bench 공격을 모두
   `safe`로 분류했다. 두 외부셋에서는 병렬 탐지의 추가 효과가 없었다.
4. 운영 임계값은 이 평가에서 다시 맞추지 않았다. 결과를 사용해 조정하려면 KoPI-Bench를
   개발셋으로 재분류하고, 별도의 미사용 최종 테스트셋을 마련해야 한다.

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
