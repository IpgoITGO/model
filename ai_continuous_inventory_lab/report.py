"""검증 결과를 숫자 그대로 정리한다."""


def report(output, result):
    chosen = result['selection']['selected']
    candidate = 'BUFFER100_FLOOR0'
    policies = ['OLD_P90', 'CLEAN_P90', candidate]
    lines = ['# 이전 전체 보정 결과 — BUFFER100_FLOOR0', '',
             '현재 작업 기준은 이전 전체 보정 BUFFER100_FLOOR0이다. P80에 과거 오차 보충량 100%를 반영하며 별도 최소 보유량은 0개, 목표 재고는 P90의 80~120%다. SKU 선택 보정은 사용하지 않는다. 아래 자동 검증 판정은 원래 기록 그대로이며, 사용자 작업 기준과 구분한다.', '',
             f'6~7월 검증에서 선택한 규칙: **{chosen}**. 8월~11월 10일에는 이 선택을 고정했다.', '',
             '발주한 물량이 도착하고 남은 재고가 다음 날로 이어진다. 14일마다 발주하며 리드타임은 5일이다. 기존 시험처럼 매주 재고를 다시 초기화하지 않는다.', '',
             '## 평가 범위', '', '| 기간 | 연속 관측 SKU | 기간 내 기록 SKU | 포함 판매 | 전체 기록 판매 |', '|---|---:|---:|---:|---:|']
    for phase, c in result['coverage'].items():
        lines.append(f"| {phase}: {c['start']}~{c['end']} | {c['skus']} | {c['observed_skus']} | {c['eligible_sales']:.0f} | {c['all_recorded_sales']:.0f} |")
    if chosen == 'CLEAN_P90':
        lines += ['', '**12개 후보 모두 6~7월 채택 조건을 통과하지 못해 기본 정책을 유지했다. 개선 정책을 채택한 결과가 아니다.**', '']
    candidate = 'BUFFER100_FLOOR0'
    base = result['metrics']['TEST']['CLEAN_P90']['actual']['ALL']
    trial = result['metrics']['TEST'][candidate]['actual']['ALL']
    development = result['metrics']['DEV']
    lines += ['', f"참고 후보 {candidate}는 이후 실제 재고 시험에서 발주 {base['order_total']}→{trial['order_total']}개, 부족 {base['unmet']:.0f}→{trial['unmet']:.0f}개, SKU 부족일 {base['miss_days']}→{trial['miss_days']}일로 개선됐다. 그러나 6~7월 실제 재고 검증의 부족일은 {development['CLEAN_P90']['actual']['ALL']['miss_days']}→{development[candidate]['actual']['ALL']['miss_days']}일이었고, 발주·평균재고도 증가해 사전 조건을 통과하지 못했다. 이 후보를 TEST 결과로 승격하지 않았다.", '']
    lines += ['', '하루라도 관측이 불완전한 SKU는 해당 기간 전체에서 제외했다. 판매 0으로 채우거나 중간 재고를 원장으로 재설정하지 않았다. 완전 관측 여부는 사후 자료 품질 필터이므로 품절·신상품이 많은 SKU로 일반화할 수 없다.', '',
              '## 8월~11월 10일 결과', '', '| 초기 재고 | 정책 | 발주 합계 | 기간 내 모의입고 | 부족 수량 | SKU별 부족일 합계 | 일평균 전체 재고 | 종료 미도착 |', '|---|---|---:|---:|---:|---:|---:|---:|']
    for mode in ('actual', 'zero'):
        for policy in policies:
            m = result['metrics']['TEST'][policy][mode]['ALL']
            lines.append(f"| {mode} | {policy} | {m['order_total']} | {m['arrivals']} | {m['unmet']:.0f} | {m['miss_days']} | {m['mean_stock']:.2f} | {m['pending_end']:.0f} |")
    lines += ['', 'OLD_P90는 원래 모델·원래 과거 출고 특징, CLEAN_P90는 정비 후 모델·판매 특징이다. 둘 다 새 연속 시뮬레이터에서 14일마다 P90 목표 재고까지 보충한다. 운영 계산식 전체를 그대로 재현한 결과는 아니다. 선택 규칙은 CLEAN_P90와 비교해 발주 정책을 바꾼 효과를 평가한다.', '',
              '## 이전 전체 보정의 월별 비교', '', '| 재고 | 월 | 정책 | 발주 | 부족 수량 | SKU 부족일 | 일평균 전체 재고 |', '|---|---|---|---:|---:|---:|---:|']
    for mode in ('actual', 'zero'):
        for period in result['metrics']['TEST']['CLEAN_P90'][mode]:
            if period == 'ALL':
                continue
            for policy in ['CLEAN_P90', candidate]:
                m = result['metrics']['TEST'][policy][mode][period]
                lines.append(f"| {mode} | {period} | {policy} | {m['order_total']} | {m['unmet']:.0f} | {m['miss_days']} | {m['mean_stock']:.2f} |")
    lines += ['', '## 모든 후보의 결과 — TEST로 재선택하지 않음', '', '| 후보 | 검증 적격 | TEST 실제 재고 발주 | 부족 | 평균재고 | TEST 재고 0 발주 | 부족 |', '|---|---|---:|---:|---:|---:|---:|']
    for policy, failure in result['selection']['failures'].items():
        a, z = [result['metrics']['TEST'][policy][mode]['ALL'] for mode in ('actual', 'zero')]
        lines.append(f"| {policy} | {'아니오' if failure else '예'} | {a['order_total']} | {a['unmet']:.0f} | {a['mean_stock']:.2f} | {z['order_total']} | {z['unmet']:.0f} |")
    lines += ['', '## 규칙', '',
              '- 판매 패턴: 직전 90일 유효 관측 60일 미만은 이력 부족, 출고 발생률 0은 무출고, 10% 이하는 간헐, 그 외 일별 변동계수 1 이하는 규칙적, 나머지는 변동으로 분류한다. 매 발주일 과거만 사용해 재분류한다.',
              '- 목표 재고 = P80 + 가중치 × 과거 양의 예측 오차 보충량. 가중치 후보는 0·0.5·1.0이며, P90의 80~120% 범위로 제한한다.',
              '- 최소 보유량 후보 0·1·2·3개는 무출고/간헐 SKU 중 마지막 출고가 180일 이내이며 유효 이력 28일 이상인 경우만 적용한다. 이 최소량은 P90 상한보다 우선한다. 최근 실제 급증 수량 13·19개에 맞춘 규칙은 만들지 않았다.',
              '- 보충량은 끝난 19일 예측의 max(실제−P80, 0) 90분위수다. SKU의 표본 n과 그룹 분포를 n/(n+10) 비중으로 결합하고, 그룹 표본 20개 미만은 전체 분포를 쓴다. 표본 기간은 SKU 내 서로 겹치지 않는다.',
              '- 5월 오차로 6~7월 규칙을 검증한다. TEST 전에는 7월 말까지 종료된 오차로 정해진 방식 그대로 보충량만 갱신한다. 오차를 산출하는 모델은 4월 말, TEST 예측 모델은 기존 7월 말 저장 모델이다.',
              '- 검증의 두 초기 재고 조건에서 월별 부족 수량·SKU 부족일이 CLEAN_P90 이하이고, 전체 발주·평균재고도 이하인 후보만 적격이다. 실제 재고 발주 최소, 평균재고 최소 순으로 고른다. 동률은 기본 정책을 우선한다.',
              '- 실제 주문 = ceil(max(목표 재고−현재 재고−미도착 발주, 0)). 목표 재고 상하한은 개별 주문 수량의 ±20% 보장을 뜻하지 않는다.', '',
              '## 가정과 한계', '',
              '- 초기 실제 재고는 각 기간 첫날 원장 값이다. TEST는 8월 1일에 한 번 시작하고 11월 10일까지 이어진다. 6~7월 검증에서 만들어진 가상 재고를 TEST로 넘기지는 않는다. 재고 0은 별도의 스트레스 시험이다.',
              '- 실제 미래 입고는 넣지 않고 정책이 생성한 입고만 사용한다. 시작 전에 발주한 미도착 물량 자료가 없어 초기 파이프라인은 0이다. 첫 4일의 피할 수 없는 부족은 별도 저장한다.',
              '- 판매 실적을 공통 수요 경로로 재생한다. 실제 품절로 기록되지 못한 잠재 수요는 알 수 없다. 시뮬레이션 부족 이후에도 과거 관측 수요로 특징을 갱신하므로 실운영의 판매 관측 손실까지 모사하지 않는다.',
              '- 폐기 및 재고 조정은 실제 날짜에 재생하며 발주 전에 미래 조정을 알 수 없다. 음수 조정은 가용 재고까지만 실행하고 실행 불가능한 조정량은 별도 기록한다. 폐기 자체를 판매 부족으로 세지 않는다.',
              '- 사용자가 실제 판매로 확인한 급증 기록은 판매로 유지한다. SKU별 임의 예외는 만들지 않았다.',
              '- 단가·유통기한·품목별 리드타임 자료는 반영하지 않았다. 재고 총개수는 자금 또는 폐기 비용과 다르다. 주문단위 1개, 고정 5일 리드타임과 14일 발주 간격이다.',
              '- 과거에 이미 살펴본 기간의 탐색적 검증이다. 새 미래 기간 성능이나 부족 0을 보장하지 않는다. 모델 재학습·운영·DB 변경은 하지 않았다.', '',
              '검산: 입고 시점, 재고 연속성, 미도착 발주 중복 방지, 판매/폐기 구분, 재고 보존식, 전체 발주=입고+종료 미도착, 과거 오차만 사용, 월별 선택 조건, 원본 해시 불변.', '']
    (output/'결과보고서.md').write_text('\n'.join(lines)+'\n')
