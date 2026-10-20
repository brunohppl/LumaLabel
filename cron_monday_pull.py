"""Run the Monday pull on a schedule — what the Pull button does, nightly.

Run by a Render Cron Job at 15:00 UTC, which is 1am Brisbane year-round
(Queensland has no daylight saving).

It calls the SAME function the button calls rather than reimplementing it,
so the two can never drift apart. The only difference is the write cap:
the button keeps a low one because a browser request has to finish, and
this has no such deadline.

Nobody is watching at 1am, so every run is recorded in the sync_runs
table and shown on the Monday page — including runs that failed, because
a row written at the start is evidence that no row at all would not give.
"""
import os
import sys
import traceback

# A higher ceiling than the button's, but still a ceiling: a bad board
# state should not rewrite every job unattended. If a night genuinely hits
# this, the summary says so and someone can look.
os.environ.setdefault('MONDAY_MAX_JOB_WRITES', '300')

import app as luma


def summarise(data):
    """The counts worth reading over a coffee, in one line each."""
    bits = []
    for label, key in (
        ('dates',      'dates_updated'),
        ('statuses',   'statuses_updated'),
        ('completed',  'completed_updated'),
        ('details',    'details_updated'),
        ('tiles made', 'tiles_created'),
        ('tiles moved','tiles_updated'),
    ):
        n = data.get(key) or 0
        if n:
            bits.append(f'{n} {label}')
    return ', '.join(bits) if bits else 'nothing to change'


def main():
    try:
        # The pull is a Flask route, so it needs a request context — but
        # this calls the real thing, not a copy of it.
        # The query string marks this as the scheduled run, so the history
        # on the Monday page distinguishes it from someone pressing Pull.
        with luma.app.test_request_context('/api/monday/pull?trigger=cron',
                                           method='POST'):
            response = luma.api_monday_pull()

        body = response[0] if isinstance(response, tuple) else response
        data = body.get_json() if hasattr(body, 'get_json') else body
        status = response[1] if isinstance(response, tuple) else 200

        if status != 200 or not data.get('success'):
            err = (data or {}).get('error', f'HTTP {status}')
            print(f'[CRON] pull failed: {err}')
            return 1

        line = summarise(data)
        extra = []
        if data.get('skipped_errors'):
            extra.append(f"{data['skipped_errors']} errors")
        if data.get('writes_remaining'):
            # The ceiling was reached — unusual, and worth a human look.
            extra.append(f"{data['writes_remaining']} left over (hit the limit)")
        if data.get('scheduled_unmatched'):
            extra.append(f"{data['scheduled_unmatched']} unmatched")

        tail = f" · {', '.join(extra)}" if extra else ''
        print(f'[CRON] {line}{tail}')
        return 0

    except Exception as e:
        # The run row is already marked failed by the pull itself, so the
        # history shows it. Render's own logs hold the traceback.
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
