"""입고 날짜·재고 연속성·미도착 발주·폐기·정책 선택 검산."""
from datetime import date, timedelta
import numpy as np
import pandas as pd
import run as r


def self_test():
    start = date(2026, 6, 1)
    dates = [start+timedelta(days=i) for i in range(9)]
    daily = pd.DataFrame(dict(product_id=1, stock_date=dates, opening_qty=0.,
                             outbound_qty=[0, 0, 0, 0, 5, 0, 0, 0, 0],
                             adjustment_qty=0., is_valid_observation=True))
    decisions = pd.DataFrame([dict(product_id=1, origin=start-timedelta(days=1)+timedelta(days=i),
                                  q90=10., old_p90=10., q80=8., buffer=2., group='INTERMITTENT',
                                  valid_history=90, days_since_outbound=5) for i in range(0, 9, 2)])
    log, orders = r.simulate(daily, decisions, start, dates[-1], r.BASE, 'zero', review=2)
    assert orders.order.tolist() == [10, 0, 0, 5, 0]
    assert log.arrival.tolist() == [0, 0, 0, 0, 10, 0, 0, 0, 0]
    assert log.closing.iloc[-1] == 5 and log.pending.iloc[-1] == 5
    assert log.unmet.sum() == 0
    m = r.metrics(log, orders)
    assert m['ALL']['order_total'] == m['2026-06']['order_total'] == 15
    assert m['ALL']['arrivals'] == 10
    actual, _ = r.simulate(daily.assign(opening_qty=20.), decisions, start, dates[-1], r.BASE, 'actual', review=2)
    assert actual.closing.iloc[-1] == 15 and actual.arrival.sum() == 0
    disposed = daily.copy()
    disposed.loc[4, ['outbound_qty', 'adjustment_qty']] = [1., -20.]
    disposal_log, _ = r.simulate(disposed, decisions, start, dates[-1], r.BASE, 'zero', review=2)
    assert disposal_log.unmet.sum() == 0 and disposal_log.unexecuted_adjustment.sum() == 11
    assert disposal_log.closing.iloc[4] == 0
    lost, _ = r.simulate(daily.assign(outbound_qty=[1, 0, 0, 0, 5, 0, 0, 0, 0]), decisions, start, dates[-1], r.BASE, 'zero', review=2)
    assert lost.unmet.sum() == lost.initial_lead_unmet.sum() == 1
    assert lost.closing.iloc[4] == 5  # 도착 전 손실을 다시 공급하지 않는다.
    row = decisions.iloc[0].to_dict()
    for policy in r.CANDIDATES:
        assert 8 <= r.target(row, policy) <= 12
    assert r.target(dict(row, q90=0., q80=0., buffer=0.), 'BUFFER0_FLOOR2') == 2
    assert r.target(dict(row, q90=0., q80=0., buffer=0., days_since_outbound=181), 'BUFFER0_FLOOR2') == 0
    baseline = dict(unmet=1, miss_days=1, order_total=10, mean_stock=5)
    result = {p: {mode: {period: dict(baseline) for period in ('ALL', '2026-06', '2026-07')}
                  for mode in r.MODES} for p in [r.BASE]+list(r.CANDIDATES)}
    assert r.choose(result)['selected'] == r.BASE
    policy = next(iter(r.CANDIDATES))
    result[policy]['actual']['ALL']['order_total'] = 9
    assert r.choose(result)['selected'] == policy
    result[policy]['zero']['2026-07']['unmet'] = 2
    assert r.choose(result)['selected'] == r.BASE
    # 그룹 오차를 쓰되 SKU별 관측 수가 늘면 해당 SKU 오차의 비중을 늘린다.
    bank = pd.DataFrame(dict(product_id=[1, 2], target_end=[start, start], error=[2., 4.], group=['DORMANT']*2))
    rows = pd.DataFrame(dict(product_id=[1, 3], origin=[start, start], group=['DORMANT']*2))
    np.testing.assert_allclose(r.buffers(rows, bank), [2/11+3.8*10/11, 3.8])
    try:
        r.buffers(rows, bank.assign(target_end=start+timedelta(days=1)))
        raise AssertionError('미래 오차를 허용함')
    except AssertionError as error:
        assert str(error) != '미래 오차를 허용함'
    print('검산 통과: 입고 5일·재고 연속·미도착 중복 방지·폐기·손실·상한·과거 오차·월별 선택', flush=True)
