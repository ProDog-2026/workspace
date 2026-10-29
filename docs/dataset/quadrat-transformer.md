# Quadrat-IPI 기반 Transformer

## 목적

기존 TF 계열 정적 탐지기와 별도로 문맥 기반 간접 프롬프트 인젝션을 탐지하는 Transformer를
학습한다. 실행 시 두 모델이 같은 메일을 독립적으로 판정하고 더 높은 판정 단계를 최종 결과로
사용한다.

## 데이터 출처와 사용 범위

- 데이터셋: [Quadrat-IPI](https://huggingface.co/datasets/mihailgribov/quadrat-ipi)
- 고정 버전: `v1.0.2`
- 라이선스: ODC-BY 1.0
- 사용 파일: `data/negatives.jsonl`, `data/positives.jsonl`
- 필터: `host_type == "email"`
- 대상 언어: 영어
- 이메일 행: 정상 21,000건, 인젝션 6,160건

Quadrat의 `clean`과 `injected`는 원래 학습/시험 분할이 아니라 서로 다른 평가 모집단이다.
따라서 이 구현은 `host_source + host_id`를 그룹 키로 삼아 80% 학습, 20% 검증으로 새로
분리한다. 같은 원본 메일에서 파생된 정상·인젝션 행은 항상 같은 쪽에 들어간다.

학습에는 그룹 분리 후 인젝션 4,935건 전량과, 결정적 해시로 고른 정상 4,935건을 사용했다.
검증 임계값 산정에는 정상 1,000건과 인젝션 1,000건을 사용했다. 이 검증 결과는 같은 Quadrat
데이터에서 나온 값이므로 독립 외부 성능으로 해석하면 안 된다.

원본 데이터는 `src/detector/data/vendor/quadrat-ipi-v1.0.2/`, 학습 가중치는
`src/detector/models/quadrat-transformer/`에 저장한다. 원본 데이터는 Git에서 제외하고 운영
가중치는 저장소에 포함한다.

## 모델과 입력 처리

- 기반 모델: `prajjwal1/bert-tiny` (BERT 2층, hidden size 128)
- 학습: CPU, 1 epoch, batch 32, learning rate `3e-5`
- 입력 길이: 최대 256 token
- 긴 메일: 1,000자 구간, 500자 중첩; 구간 중 최대 확률을 메일 점수로 사용
- 학습 인젝션: `inj_span`을 중심으로 구간을 잘라 실제 공격 문구가 입력에 포함되게 함
- 실행 의존성: PyTorch와 Transformers만 사용하며 LLM API는 호출하지 않음

## 2026-10-26 검증 결과

| 판정 기준 | 임계값 | 재현율 | 정밀도 | F1 | 정상 오탐률 |
| --- | ---: | ---: | ---: | ---: | ---: |
| suspicious 이상 | 0.939322 | 98.7% | 95.36% | 97.00% | 4.8% |
| danger | 0.942692 | 96.0% | 99.79% | 97.86% | 0.2% |

ROC AUC는 `0.997089`다. 전체 수치는
`src/detector/reports/quadrat-transformer-metrics.json`에 기록한다.

기존 수동 30건(`manual-evaluation-2026-10-12.jsonl`)을 다시 검사하면 TF 계열 모델과 결합
모델 모두 20/30(66.7%)이었다. Transformer 단독은 15/30이었고, 짧은 직접 명령 위주인 이
표본에서는 결합 결과를 추가로 개선하지 못했다. 반면 Quadrat 검증 행 중 기존 모델이 `safe`로
놓친 간접 인젝션을 Transformer가 `danger`로 올리는 사례는 확인했다.

## 한계

- 현재 학습·검증 데이터는 영어뿐이며 한국어 성능은 측정하지 않았다.
- 임계값도 Quadrat 내부 검증 표본에서 정했으므로 별도 외부 이메일 데이터로 다시 평가해야 한다.
- 짧고 노골적인 직접 명령은 Transformer가 놓칠 수 있다. 이 경우 기존 TF 계열 모델의 판정이
  최종 결과에 반영된다.
- 두 모델의 확률은 보정 방식이 달라 확률 숫자의 최댓값이 아니라 판정 단계의 최댓값을 사용한다.
