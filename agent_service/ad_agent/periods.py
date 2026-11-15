"""Business-day boundaries shared by routing and every reporting tool."""
from datetime import date


def bounded_period(start_date, end_date, business_day):
    start, end, today = map(date.fromisoformat, (start_date, end_date, business_day))
    if end < start:
        raise ValueError('INVALID_RANGE')
    info = {'requested_start_date': start.isoformat(), 'requested_end_date': end.isoformat(),
            'business_day': today.isoformat(), 'future_end': end > today,
            'entirely_future': start > today, 'effective_end_date': min(end, today).isoformat()}
    if start > today:
        info['notice'] = f'您输入的时间范围（{start}至{end}）全部晚于今天（{today}），目前没有未来的统计数据。请调整开始日期，我再帮您查询。'
    elif end > today:
        info['notice'] = f'您输入的截止日期（{end}）晚于今天（{today}），时间范围可能有误。以下按{start}至今天（{today}）汇总已有记录；未来日期不纳入统计。'
    return start, min(end, today), info
