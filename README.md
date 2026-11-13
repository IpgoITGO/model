# 재고 수요 예측과 전체 보정 실험

LightGBM 판매 수량 예측에 SKU별 과거 예측 오차를 반영하고, 재고·발주·입고를 날짜별로 이어가며 검증하는 코드다. 현재 비교 기준은 **`BUFFER100_FLOOR0` 전체 보정**이다. 분류와 회귀를 결합한 하이브리드 모델은 아니다.

## 현재 기준

현재 비교 기준은 `BUFFER100_FLOOR0`다. 실험 코드는 기본 P90 및 보충량·최소 보유량 후보를 비교하고, 과거 검증 조건에 따라 자동 선택한다. 비교 기준 버전과 자동 선택 결과는 다를 수 있다. 전체 보정만 강제로 선택하는 운영 프로그램은 아니다.

이 공개 저장소는 코드와 실행 문서를 제공한다. 내부 데이터에서 산출한 결과 수치·집계 보고서·SKU별 상세 결과·학습 모델은 포함하지 않는다. 사용 권한이 있는 로컬 자료로 실행하면 해당 환경에 결과와 보고서를 생성한다.

## 계산 방식

```text
과거 양의 오차 = max(19일 실제 판매 − 당시 P80 예측, 0)
SKU 비중 = 오차 표본 수 n / (n + 10)
보충량 = SKU 비중 × SKU 오차의 90분위수
       + (1 − SKU 비중) × 판매 패턴 그룹 오차의 90분위수

목표 재고 = min(P90 × 1.2, max(P90 × 0.8, P80 + 보충량))
발주량 = ceil(max(목표 재고 − 현재 재고 − 미도착 발주, 0))
```

그룹 표본이 20개 미만이면 전체 오차 분포를 쓴다. `BUFFER100`은 오차 보충량을 100% 반영한다는 뜻이며 주문량을 100% 늘리는 의미가 아니다. `FLOOR0`은 별도 최소 보유량을 추가하지 않는다. 80~120% 제한은 목표 재고 기준이며 주문량의 ±20% 제한이 아니다.

입고 리드타임 5일, 발주 간격 14일, 주문 단위 1개를 사용한다. 초기 재고는 기간 첫날에 한 번만 설정한다. 실제 미래 입고 대신 정책이 생성한 입고를 반영하며, 판매 부족은 다음 날 주문으로 이월하지 않는다. 미도착 발주를 차감해 중복 주문을 방지한다.

## 구성

```text
ai_continuous_inventory_lab/
  run.py          # 예측 호출, 오차 보정, 연속 재고 재생, 후보 비교
  test_run.py     # 합성 자료를 이용한 경계 조건 검산
  verify.py       # 저장된 상세 결과와 모델 예측 재검산
  report.py       # 실행 결과 보고서 생성
ai_forecast_lab/
  experiment.py   # 원래 실험의 특징 생성·입력 행렬·분위수 후처리
requirements.txt
```

기존 전체 보정의 계산·시뮬레이션을 유지했다. 배포용 정리는 공통 함수 세 개를 원본과 동일하게 분리하고, 입력 데이터 위치와 관계없이 저장소의 공통 코드를 불러오도록 바꾼 것이다. 원본 실험 디렉토리는 변경하지 않는다. 원복한 SKU 선택 보정 실행 코드는 포함하지 않는다.

## 설치와 빠른 검산

검증 환경: Python 3.14.6, NumPy 2.5.4, pandas 3.0.3, LightGBM 4.6.0.

```sh
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python ai_continuous_inventory_lab/run.py --self-test
```

합성 검산은 외부 데이터 없이 실행된다. 입고 날짜, 재고 연속성, 미도착 주문, 폐기 차감, 도착 전 손실, 목표 상한, 미래 오차 차단, 월별 정책 선택을 확인한다.

## 실제 자료로 재실행

공개 저장소에는 원본 수불부, 정비된 일별 자료, 학습 모델 파일, SKU별 상세 결과, 인증정보를 포함하지 않는다. 전체 재실행에는 접근 권한이 있는 로컬 자료가 필요하다. 아래 구조로 준비하거나 동일한 구조의 기존 프로젝트 경로를 `--project-root`로 지정한다.

```text
private-assets/
  ai_data_cleanup_lab/results/cleaned_daily.csv.gz
  ai_forecast_lab/results/models/
    tune/                     # 4월 말 원래 모델
      q50.txt, q80.txt, q90.txt, metadata.json
    test/                     # 7월 말 원래 모델
      q50.txt, q80.txt, q90.txt, metadata.json
  ai_cleaned_forecast_lab/results/models/
    2026-04-30/regression/
      q50.txt, q80.txt, q90.txt, metadata.json
    2026-07-31/regression/
      q50.txt, q80.txt, q90.txt, metadata.json
```

정비 CSV의 필수 필드는 `product_id`, `stock_date`, `opening_qty`, `outbound_qty`, `adjustment_qty`, `is_valid_observation`, `raw_outbound_qty`, `raw_adjustment_qty`다. 기존 자료에는 `sku_barcode`와 폐기 구분 등 추가 필드도 있다. 상품 ID는 저장 모델의 ID와 같아야 한다. 모델 메타데이터에는 `cutoff`, `product_train_counts`가 필요하다.

```sh
python ai_continuous_inventory_lab/run.py \
  --project-root ./private-assets \
  --output ./artifacts/run_01

python ai_continuous_inventory_lab/verify.py \
  --project-root ./private-assets \
  --output ./artifacts/run_01
```

기존 출력 폴더는 덮어쓰지 않으므로 재실행 때 새 이름을 사용한다. 실행은 저장 모델을 재사용하며 새 학습을 하지 않는다. `comparison.json`, 사전 선택 기록, 발주/일별 재고 CSV와 `결과보고서.md`를 생성한다. 상세 산출물과 비공개 입력 경로는 Git에서 제외한다.

## 데이터와 평가

SKU별 하루 출고 합계가 100개 이상이면 전량 폐기로 분리하는 정비 규칙을 사용한다. 판매 목표에서 제외하되 재고 차감은 유지하며, 입고량에는 이 규칙을 적용하지 않는다. 실제 판매로 확인된 기록은 판매로 유지한다.

학습 표본은 특정 SKU·기준일에서 이후 19일 판매량을 예측하는 자료다. 같은 SKU가 여러 날짜에 등장하고 학습 목표 기간도 겹칠 수 있다. 저장 모델을 재사용하므로 이 프로그램 자체는 새 학습을 하지 않는다. 오차 보충량 산정은 같은 SKU의 기간이 겹치지 않는 완료된 예측을 사용한다.

각 평가 기간의 모든 날짜가 관측된 품목만 포함한다. 이는 사후 품질 필터이며 실제 품절로 기록되지 않은 잠재 수요를 복원하지 않는다. 초기 미도착 주문은 0으로 가정하고 단가·유통기한·실제 SKU별 리드타임도 반영하지 않는다. 독립적인 미래 기간 검증 없이 운영 성능이나 부족 0을 보장할 수 없다.

## 검증 기록

공개용 패키징 후 합성 검산과 접근 권한이 있는 로컬 상세 결과의 재검산을 통과했다. 입고·재고 연속성·미도착 주문·모델 재로딩·미래 정보 차단을 확인했다. 특징 생성·입력 행렬·후처리 및 발주·재고 계산 함수는 원본과 AST가 일치한다. 원본 실험 디렉토리를 변경하거나 모델을 다시 학습하지 않았다.
