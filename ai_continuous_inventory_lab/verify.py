"""저장 결과·시간 경계·특징의 미래 차단을 다시 검산한다."""
import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import run as r


def verify(root, output):
    r.configure(root)
    result = json.loads((output/'comparison.json').read_text())
    assert r.choose(result['metrics']['DEV']) == result['selection']
    assert json.loads((output/'selection_before_test.json').read_text()) == result['selection']
    log = pd.read_csv(output/'daily_replay.csv.gz', float_precision='round_trip')
    orders = pd.read_csv(output/'orders.csv.gz', float_precision='round_trip')
    keys = ['phase', 'policy', 'mode', 'product_id']
    assert not log.duplicated(keys+['date']).any()
    assert not orders.duplicated(keys+['origin']).any()
    assert (log[['opening', 'arrival', 'shipped', 'unmet', 'closing', 'pending']] >= 0).all().all()
    np.testing.assert_allclose(log.opening+log.arrival-log.shipped+log.applied_adjustment, log.closing)
    np.testing.assert_allclose(log.demand, log.shipped+log.unmet)
    previous_close = log.groupby(keys).closing.shift()
    np.testing.assert_allclose(log.loc[previous_close.notna(), 'opening'], previous_close.dropna())
    grouped = log.groupby(keys)
    ending = grouped.last()
    arrivals = grouped.arrival.sum()
    purchases = orders.groupby(keys).order.sum()
    np.testing.assert_allclose(purchases.sort_index(), (arrivals+ending.pending).sort_index())
    for (phase, policy, mode), rows in log.groupby(['phase', 'policy', 'mode']):
        order_rows = orders[(orders.phase == phase) & (orders.policy == policy) & (orders['mode'] == mode)]
        computed = r.metrics(rows, order_rows)
        for period, metric in computed.items():
            expected = result['metrics'][phase][policy][mode][period]
            for key, value in metric.items():
                np.testing.assert_allclose(value, expected[key], atol=1e-9)
    for phase, cutoff, end in [('dev', '2026-04-30', date(2026, 5, 31)),
                               ('test', '2026-07-31', date(2026, 7, 31))]:
        decisions = pd.read_csv(output/f'{phase}_decisions.csv.gz', float_precision='round_trip')
        bank = pd.read_csv(output/f'{phase}_residuals.csv', float_precision='round_trip')
        for rows in (decisions, bank):
            rows['origin'] = pd.to_datetime(rows.origin).dt.date
            rows['target_end'] = pd.to_datetime(rows.target_end).dt.date
        assert bank.target_end.max() <= end <= decisions.origin.min()
        assert (bank.origin >= date(2026, 4, 30)).all()
        for _, sku in bank.groupby('product_id'):
            origins = sorted(sku.origin)
            assert all(b-a >= timedelta(days=19) for a, b in zip(origins, origins[1:]))
        np.testing.assert_allclose(r.buffers(decisions, bank), decisions.buffer, atol=1e-12)
        forecast = r.forecast(decisions, root/f'ai_cleaned_forecast_lab/results/models/{cutoff}/regression')
        np.testing.assert_allclose(forecast, decisions[['q50', 'q80', 'q90']], atol=1e-12)
        changed = decisions.assign(actual=999999.)
        for policy in r.CANDIDATES:
            np.testing.assert_array_equal([r.target(row, policy) for row in decisions.to_dict('records')],
                                          [r.target(row, policy) for row in changed.to_dict('records')])
    daily = pd.read_csv(root/'ai_data_cleanup_lab/results/cleaned_daily.csv.gz', float_precision='round_trip')
    daily.stock_date = pd.to_datetime(daily.stock_date).dt.date
    sku = daily[daily.product_id == 52].copy()
    altered = sku.copy()
    origin = date(2026, 10, 9)
    altered.loc[altered.stock_date > origin, 'outbound_qty'] = 999999.
    before, after = [r.lab.features_and_labels(s) for s in (sku, altered)]
    np.testing.assert_allclose(before.loc[before.origin <= origin, r.lab.FEATURES],
                               after.loc[after.origin <= origin, r.lab.FEATURES], equal_nan=True)
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == value
               for p, value in result['input_hashes'].items())
    checks = dict(saved_daily_rows=len(log), saved_orders=len(orders), csv_totals=True,
                  continuous_inventory=True, pending_orders=True, forecast_reload=True,
                  nonoverlap_residuals=True, future_features_blocked=True,
                  future_target_unused=True, selection_reproduced=True, source_hashes=True)
    r.write_json(output/'verification.json', checks)
    print('저장 검산 통과:', checks)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent/'results')
    args = parser.parse_args()
    verify(args.project_root, args.output)
