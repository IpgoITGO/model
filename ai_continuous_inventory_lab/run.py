"""저장 모델과 과거 오차를 이용한 연속 재고·발주 정책 검증."""
import argparse
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
import lightgbm as lgb
import numpy as np
import pandas as pd

LEAD, REVIEW, HORIZON = 5, 14, 19
BASE = 'CLEAN_P90'
CANDIDATES = {f'BUFFER{int(w*100)}_FLOOR{floor}': (w, floor)
              for w in (0., .5, 1.) for floor in (0, 1, 2, 3)}
MODES = ('actual', 'zero')


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def configure(root):
    global lab
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent/'ai_forecast_lab'))
    import experiment as lab


def features(daily):
    return pd.concat([lab.features_and_labels(s).reset_index(drop=True)
                      for _, s in daily.groupby('product_id')], ignore_index=True)


def groups(rows):
    rate = rows.positive_rate_90.to_numpy()
    cv = rows.std_90.to_numpy()/np.maximum(rows.mean_90.to_numpy(), 1e-12)
    return np.where(rows.valid_days_90 < 60, 'SHORT_HISTORY',
           np.where(rate == 0, 'DORMANT', np.where(rate <= .1, 'INTERMITTENT',
           np.where(cv <= 1, 'STEADY', 'VARIABLE'))))


def forecast(rows, folder):
    meta = json.loads((folder/'metadata.json').read_text())
    assert meta['cutoff'] <= str(rows.origin.min())
    models = [lgb.Booster(model_file=str(folder/f'q{q}.txt')) for q in (50, 80, 90)]
    values = np.column_stack([m.predict(lab.matrix(rows), num_threads=1) for m in models])
    counts = {int(k): v for k, v in meta['product_train_counts'].items()}
    return lab.postprocess(values, rows, counts)[0]


def residual_bank(clean, root, cutoff):
    # shortcut: 4월 말 모델의 종료된 오차를 사용한다. 새 기간이 쌓이면 월별 OOF 오차로 교체한다.
    origins = []
    origin = date(2026, 4, 30)
    while origin+timedelta(days=HORIZON) <= cutoff:
        origins.append(origin)
        origin += timedelta(days=HORIZON)
    rows = clean[clean.origin.isin(origins) & clean.actual.notna() & (clean.valid_days_28 >= 7)].copy()
    assert len(rows) and (rows.target_end <= cutoff).all()
    rows['q80'] = forecast(rows, root/'ai_cleaned_forecast_lab/results/models/2026-04-30/regression')[:, 1]
    rows['error'] = np.maximum(rows.actual-rows.q80, 0)
    rows['group'] = groups(rows)
    return rows[['product_id', 'origin', 'target_end', 'actual', 'q80', 'error', 'group']]


def buffers(rows, bank):
    assert bank.target_end.max() <= rows.origin.min()
    pooled = float(bank.error.quantile(.9))
    group = {g: float(s.error.quantile(.9)) if len(s) >= 20 else pooled
             for g, s in bank.groupby('group')}
    sku = {pid: (len(s), float(s.error.quantile(.9))) for pid, s in bank.groupby('product_id')}
    result = []
    for row in rows.itertuples():
        count, local = sku.get(row.product_id, (0, pooled))
        weight = count/(count+10)
        result.append(weight*local+(1-weight)*group.get(row.group, pooled))
    return np.asarray(result)


def target(row, policy):
    if policy in ('OLD_P90', BASE):
        return float(row['old_p90'] if policy == 'OLD_P90' else row['q90'])
    weight, floor = CANDIDATES[policy]
    floor = floor if (row['group'] in ('DORMANT', 'INTERMITTENT') and
                     row['valid_history'] >= 28 and row['days_since_outbound'] <= 180) else 0
    lower, upper = .8*row['q90'], max(1.2*row['q90'], floor)
    return max(floor, min(upper, max(lower, row['q80']+weight*row['buffer'])))


def simulate(daily, decisions, start, end, policy, mode, lead=LEAD, review=REVIEW):
    """초기에만 재고 설정. 전일 마감 발주→lead일 뒤 아침 입고, 부족은 이월하지 않는다."""
    decision_map = {(int(r.product_id), r.origin): r._asdict() for r in decisions.itertuples(index=False)}
    logs, orders = [], []
    for pid, sku in daily.groupby('product_id', sort=True):
        sku = sku.sort_values('stock_date')
        assert sku.stock_date.tolist() == list(pd.date_range(start, end).date)
        assert sku.is_valid_observation.all()
        stock = float(sku.opening_qty.iloc[0]) if mode == 'actual' else 0.
        assert stock >= 0
        queue = {}
        for i, row in enumerate(sku.itertuples(index=False)):
            when = row.stock_date
            if i % review == 0:
                origin = when-timedelta(days=1)
                f = decision_map[(int(pid), origin)]
                level = target(f, policy)
                pending = sum(queue.values())
                qty = max(0, math.ceil(level-stock-pending-1e-12))
                arrival = origin+timedelta(days=lead)
                queue[arrival] = queue.get(arrival, 0)+qty
                orders.append(dict(product_id=int(pid), origin=str(origin), review_start=str(when), arrival=str(arrival),
                                   target=level, stock=stock, pending=pending, order=qty, group=f['group']))
            opening = stock
            arrival = queue.pop(when, 0)
            stock += arrival
            demand = float(row.outbound_qty)
            shipped = min(stock, demand)
            unmet = demand-shipped
            stock -= shipped
            adjustment = float(row.adjustment_qty)
            applied = max(adjustment, -stock)
            stock += applied
            assert stock >= 0 and np.isclose(stock, opening+arrival-shipped+applied)
            logs.append(dict(product_id=int(pid), date=str(when), opening=opening, arrival=arrival,
                             demand=demand, shipped=shipped, unmet=unmet, miss=int(unmet > 0),
                             requested_adjustment=adjustment, applied_adjustment=applied,
                             unexecuted_adjustment=applied-adjustment, closing=stock,
                             pending=sum(queue.values()), initial_lead_unmet=unmet if i < lead-1 else 0))
    return pd.DataFrame(logs), pd.DataFrame(orders)


def metrics(log, orders):
    months = sorted(log.date.str[:7].unique())
    result = {}
    for period in ['ALL']+months:
        a = log if period == 'ALL' else log[log.date.str.startswith(period)]
        b = orders if period == 'ALL' else orders[orders.review_start.str.startswith(period)]
        last = a[a.date == a.date.max()]
        days = a.date.nunique()
        result[period] = dict(order_total=int(b.order.sum()), arrivals=int(a.arrival.sum()),
                             unmet=float(a.unmet.sum()), miss_days=int(a.miss.sum()),
                             mean_stock=float(a.closing.sum()/days), ending_stock=float(last.closing.sum()),
                             pending_end=float(last.pending.sum()), demand=float(a.demand.sum()),
                             initial_lead_unmet=float(a.initial_lead_unmet.sum()),
                             unexecuted_adjustment=float(a.unexecuted_adjustment.sum()),
                             sku_days=len(a), skus=int(a.product_id.nunique()))
    return result


def choose(results):
    failures = {}
    for policy in CANDIDATES:
        failed = []
        for mode in MODES:
            for period, candidate in results[policy][mode].items():
                baseline = results[BASE][mode][period]
                for key in ('unmet', 'miss_days'):
                    if candidate[key] > baseline[key]+1e-9:
                        failed.append(f'{mode}/{period}/{key}')
                if period == 'ALL':
                    for key in ('order_total', 'mean_stock'):
                        if candidate[key] > baseline[key]+1e-9:
                            failed.append(f'{mode}/{period}/{key}')
        failures[policy] = failed
    eligible = [p for p, f in failures.items() if not f]
    choices = [BASE]+eligible
    selected = min(choices, key=lambda p: (results[p]['actual']['ALL']['order_total'],
                    results[p]['actual']['ALL']['mean_stock'], p != BASE))
    return dict(selected=selected, eligible=eligible, failures=failures)


def prepare_period(daily, clean, raw, root, start, end, cutoff, bank):
    part = daily[daily.stock_date.between(start, end)]
    count = part.groupby('product_id').agg(days=('stock_date', 'size'), valid=('is_valid_observation', 'sum'))
    dates = [start-timedelta(days=1)+timedelta(days=n) for n in range(0, (end-start).days+1, REVIEW)]
    possible = count.index[(count.days == (end-start).days+1) & (count.valid == count.days)]
    good_first = clean[(clean.origin == dates[0]) & (clean.valid_days_28 >= 7)].product_id
    ids = sorted(set(possible) & set(good_first))
    rows = clean[clean.product_id.isin(ids) & clean.origin.isin(dates)].copy().sort_values(['origin', 'product_id'])
    legacy = raw[raw.product_id.isin(ids) & raw.origin.isin(dates)].copy().sort_values(['origin', 'product_id'])
    assert len(rows) == len(ids)*len(dates) and np.array_equal(rows[['origin', 'product_id']], legacy[['origin', 'product_id']])
    q = forecast(rows, root/f'ai_cleaned_forecast_lab/results/models/{cutoff}/regression')
    old_dir = 'tune' if cutoff == '2026-04-30' else 'test'
    old = forecast(legacy, root/f'ai_forecast_lab/results/models/{old_dir}')
    rows['q50'], rows['q80'], rows['q90'], rows['old_p90'] = q[:, 0], q[:, 1], q[:, 2], old[:, 2]
    rows['group'] = groups(rows)
    rows['buffer'] = buffers(rows, bank)
    selected = part[part.product_id.isin(ids)]
    coverage = dict(skus=len(ids), observed_skus=int(part.product_id.nunique()),
                    eligible_sales=float(selected.outbound_qty.sum()), all_recorded_sales=float(part.outbound_qty.sum()),
                    excluded_skus=sorted(set(part.product_id)-set(ids)), start=str(start), end=str(end))
    return selected, rows, coverage


def main(args):
    configure(args.project_root)
    from test_run import self_test
    self_test()
    if args.self_test:
        return
    root, output = args.project_root.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = root/'ai_data_cleanup_lab/results/cleaned_daily.csv.gz'
    protected = [source, Path(lab.__file__)]
    for folder in [root/'ai_forecast_lab/results/models', root/'ai_cleaned_forecast_lab/results/models']:
        protected += [p for p in folder.rglob('*') if p.is_file()]
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    daily = pd.read_csv(source, dtype={'sku_barcode': str}, float_precision='round_trip')
    daily.stock_date = pd.to_datetime(daily.stock_date).dt.date
    assert not daily.duplicated(['product_id', 'stock_date']).any()
    assert np.isfinite(daily[['opening_qty', 'outbound_qty', 'adjustment_qty']]).all().all()
    raw_daily = daily.assign(outbound_qty=daily.raw_outbound_qty, adjustment_qty=daily.raw_adjustment_qty)
    clean, raw = features(daily), features(raw_daily)
    write_json(output/'protocol.json', dict(candidates=CANDIDATES, lead=LEAD, review=REVIEW,
               calibration='2026-05, nonoverlapping completed 19-day errors', validation='2026-06-01/2026-07-31',
               test='2026-08-01/2026-11-10', target_band=[.8, 1.2], floor_exception=True,
               primary='actual stock, order quantity', guards='unmet and SKU shortage days in each development month, both modes; total orders and mean stock no higher than CLEAN_P90',
               cohort='complete valid observation in each period and >=7 valid prior 28 days; retrospective coverage filter'))
    all_metrics, coverage, selection = {}, {}, None
    logs, orders = [], []
    for phase, start, end, cutoff, calibration_end in [
        ('DEV', date(2026, 6, 1), date(2026, 7, 31), '2026-04-30', date(2026, 5, 31)),
        ('TEST', date(2026, 8, 1), date(2026, 11, 10), '2026-07-31', date(2026, 7, 31))]:
        if phase == 'TEST':
            selection = choose(all_metrics['DEV'])
            write_json(output/'selection_before_test.json', selection)
            print('TEST 전 선택:', selection['selected'], flush=True)
        bank = residual_bank(clean, root, calibration_end)
        bank.to_csv(output/f'{phase.lower()}_residuals.csv', index=False)
        part, predictions, coverage[phase] = prepare_period(daily, clean, raw, root, start, end, cutoff, bank)
        predictions.assign(phase=phase).to_csv(output/f'{phase.lower()}_decisions.csv.gz', index=False)
        all_metrics[phase] = {}
        for policy in ['OLD_P90', BASE]+list(CANDIDATES):
            all_metrics[phase][policy] = {}
            for mode in MODES:
                log, order = simulate(part, predictions, start, end, policy, mode)
                all_metrics[phase][policy][mode] = metrics(log, order)
                logs.append(log.assign(phase=phase, policy=policy, mode=mode))
                orders.append(order.assign(phase=phase, policy=policy, mode=mode))
        print(phase, 'coverage:', coverage[phase]['skus'], 'SKU', flush=True)
        for policy in ['OLD_P90', BASE]+([selection['selected']] if selection else []):
            print(policy, all_metrics[phase][policy]['actual']['ALL'], flush=True)
    frame, order_frame = pd.concat(logs, ignore_index=True), pd.concat(orders, ignore_index=True)
    frame.to_csv(output/'daily_replay.csv.gz', index=False)
    order_frame.to_csv(output/'orders.csv.gz', index=False)
    # 종료 후 도착하는 발주도 집계해 말기 발주를 시험 밖으로 숨기지 않는다.
    for (phase, policy, mode, pid), a in frame.groupby(['phase', 'policy', 'mode', 'product_id']):
        b = order_frame[(order_frame.phase == phase) & (order_frame.policy == policy) & (order_frame['mode'] == mode) & (order_frame.product_id == pid)]
        assert np.isclose(b.order.sum(), a.arrival.sum()+a.pending.iloc[-1])
        assert np.isclose(a.opening.iloc[0]+a.arrival.sum()-a.shipped.sum()+a.applied_adjustment.sum(), a.closing.iloc[-1])
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == value for p, value in hashes.items())
    result = dict(metrics=all_metrics, coverage=coverage, selection=selection, input_hashes=hashes,
                  checks=dict(unit_checks=True, inventory_conservation=True, pending_orders_accounted=True,
                              original_files_unchanged=True, selection_before_test=True))
    write_json(output/'comparison.json', result)
    from report import report
    report(output, result)
    print('완료:', output, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent/'results')
    parser.add_argument('--self-test', action='store_true')
    main(parser.parse_args())
