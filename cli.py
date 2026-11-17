"""brewlog: a command-line tool for brewery batches, backed by Pixeltable.

    pxt schema update app.py brew
    python cli.py seed                    # 6 batches + a fermentation curve of hydrometer readings each
    python cli.py board                   # batches ranked by ABV (computed with expression UDFs)
    python cli.py curve B-102             # one batch's readings: corrected gravity, Plato, stage
    python cli.py read B-104 1.034 21.5   # log a reading (gravity, temp C)
    python cli.py finish B-104 1.012      # record the final gravity -> ABV and attenuation fill in
    python cli.py phase slowing           # readings in a fermentation stage
    python cli.py calc 1.062 1.014        # the same maths as a table-as-UDF (pxt.udf(table, return_value=...))
    python cli.py doctor                  # drive the pxt CLI itself: ls / describe / columns / idxs / rows / count / history
    python cli.py session                 # pxt whoami, and pxt logout in a throwaway Pixeltable home
    python cli.py feedback 'what was missing'   # preview a gap report for the Pixeltable team (never sends)

Every command takes --target: a local catalog dir (default 'brew') or a Pixeltable Cloud URI pxt://<org>:<db>.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import subprocess
import sys
import tempfile

BREWED = dt.date(2026, 11, 1)

BATCHES = [
    ('B-101', 'Harbor Pale', 'pale ale', 1.052, 1.011, 40.0),
    ('B-102', 'Foghorn Stout', 'stout', 1.068, 1.017, 30.0),
    ('B-103', 'Lantern Lager', 'lager', 1.048, 1.010, 60.0),
    ('B-104', 'Copper Saison', 'saison', 1.058, None, 25.0),
    ('B-105', 'Tide IPA', 'ipa', 1.066, None, 50.0),
    ('B-106', 'Dockside Wheat', 'wheat', 1.046, 1.012, 35.0),
]


def is_hosted(target: str) -> bool:
    return target.split(':', 1)[0] == 'pxt'


def start(target: str):
    import app

    app.TableModel.bind_all(target)
    return app


def show(rows) -> None:
    rows = list(rows)
    if not rows:
        print('(none)')
        return
    cols = list(rows[0].keys())

    def cell(v):
        return f'{v:.4f}' if isinstance(v, float) else str(v)

    width = {c: max(len(c), *(len(cell(r[c])) for r in rows)) for c in cols}
    print('  '.join(c.ljust(width[c]) for c in cols))
    for r in rows:
        print('  '.join(cell(r[c]).ljust(width[c]) for c in cols))


def curve_for(og: float, fg: float | None, rng: random.Random, days: int = 10):
    """A plausible gravity curve from OG down to the final gravity (or part-way for batches still fermenting)."""
    end = fg if fg is not None else og - (og - 1.0) * 0.55
    out = []
    for d in range(days):
        frac = 1 - 0.5 ** (d / 2.2)  # fast drop, then flatten
        g = og - (og - end) * frac + rng.uniform(-0.0008, 0.0008)
        out.append((d, round(g, 4)))
    return out


def cmd_seed(args) -> None:
    app = start(args.target)
    b, r = app.Batches.table, app.Readings.table
    st = b.insert(
        [
            {'batch_id': i, 'name': n, 'style': s, 'brewed_on': BREWED + dt.timedelta(days=k), 'volume_l': v, 'og': og, 'fg': fg}
            for k, (i, n, s, og, fg, v) in enumerate(BATCHES)
        ],
        on_error='abort',
    )
    rng = random.Random(20261117)
    rows = []
    for k, (i, _, _, og, fg, _) in enumerate(BATCHES):
        for d, g in curve_for(og, fg, rng):
            at = dt.datetime.combine(BREWED + dt.timedelta(days=k + d), dt.time(9))
            rows.append(
                {'reading_id': f'{i}-{d:02d}', 'batch_id': i, 'taken_at': at, 'gravity': g,
                 'temp_c': round(rng.uniform(16.0, 24.0), 1), 'og': og}
            )
    sr = r.insert(rows, on_error='abort')
    print(f'seeded {st.num_rows} batches, {sr.num_rows} readings ({st.num_excs + sr.num_excs} errors)')


def cmd_board(args) -> None:
    show(start(args.target).gravity_board().collect())


def cmd_curve(args) -> None:
    show(start(args.target).batch_curve(args.batch_id).collect())


def cmd_read(args) -> None:
    app = start(args.target)
    b, r = app.Batches.table, app.Readings.table
    found = list(b.where(b.batch_id == args.batch_id).select(b.og).collect())
    if not found:
        sys.exit(f'unknown batch {args.batch_id}')
    at = dt.datetime.fromisoformat(args.at) if args.at else dt.datetime.now()
    rid = f'{args.batch_id}-{at:%Y%m%d%H%M%S}'
    r.insert([{'reading_id': rid, 'batch_id': args.batch_id, 'taken_at': at, 'gravity': args.gravity,
               'temp_c': args.temp, 'og': found[0]['og']}], on_error='abort')
    row = list(r.where(r.reading_id == rid).select(r.corrected, r.degrees_plato, r.phase).collect())[0]
    print(f"{args.batch_id}: corrected {row['corrected']:.4f}  {row['degrees_plato']:.2f} Plato  stage={row['phase']}")


def cmd_finish(args) -> None:
    app = start(args.target)
    b = app.Batches.table
    b.update({'fg': args.fg}, where=b.batch_id == args.batch_id)
    show(list(b.where(b.batch_id == args.batch_id).select(b.batch_id, b.og, b.fg, b.abv, b.attenuation).collect()))


def cmd_phase(args) -> None:
    show(start(args.target).phase_counts(args.phase).collect())


def cmd_calc(args) -> None:
    """A throwaway scratch table wrapped as a UDF: pxt.udf(table, return_value=col). Local targets only."""
    import pixeltable as pxt

    pxt.create_dir('brew_scratch', if_exists='ignore')
    pxt.drop_table('brew_scratch/calc', force=True, if_not_exists='ignore')
    pxt.drop_table('brew_scratch/probe', force=True, if_not_exists='ignore')
    calc = pxt.create_table('brew_scratch/calc', {'og': pxt.Float, 'fg': pxt.Float})
    calc.add_computed_column(att=(calc.og - calc.fg) / (calc.og - 1.0) * 100)
    calc.add_computed_column(abv=(calc.og - calc.fg) * 131.25)
    fn = pxt.udf(calc, return_value=calc.abv)
    probe = pxt.create_table('brew_scratch/probe', {'og': pxt.Float, 'fg': pxt.Float})
    probe.add_computed_column(abv_via_table=fn(og=probe.og, fg=probe.fg))
    probe.insert([{'og': args.og, 'fg': args.fg}])
    print(list(probe.select(probe.og, probe.fg, probe.abv_via_table).collect())[0])
    print(f'rows inserted into the wrapped table as a side effect: {calc.count()}')
    pxt.drop_dir('brew_scratch', force=True)


def run(cmd: list[str], env: dict | None = None) -> int:
    p = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, **(env or {})})
    out = (p.stdout + p.stderr).strip().splitlines()
    print(f"$ {' '.join(cmd)}   -> exit {p.returncode}")
    for line in out[:6]:
        print('   ' + line)
    if len(out) > 6:
        print(f'   ... ({len(out) - 6} more lines)')
    return p.returncode


def cmd_doctor(args) -> None:
    t = args.target
    for c in (['ls', t], ['describe', f'{t}/batches'], ['columns', f'{t}/readings'], ['computed', f'{t}/readings'],
              ['idxs', f'{t}/batches'], ['count', f'{t}/readings'], ['rows', f'{t}/batches', '-n', '2'],
              ['history', f'{t}/batches'], ['errors', f'{t}/readings']):
        run(['pxt', *c])


def cmd_session(args) -> None:
    """whoami against the real environment; logout only inside a throwaway Pixeltable home."""
    run(['pxt', 'whoami'])
    with tempfile.TemporaryDirectory() as home:
        env = {'HOME': home, 'PIXELTABLE_HOME': os.path.join(home, '.pixeltable')}
        run(['pxt', 'logout'], env)
        run(['pxt', 'logout'], env)
        run(['pxt', 'whoami'], {**env, 'PIXELTABLE_API_KEY': ''})


def cmd_feedback(args) -> None:
    """Preview a gap report. Never passes --send: a person must read the preview first."""
    run(['pxt', 'feedback', '--kind', 'gap', args.text])


def main() -> None:
    ap = argparse.ArgumentParser(prog='brewlog', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--target', default='brew')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('seed').set_defaults(fn=cmd_seed)
    sub.add_parser('board').set_defaults(fn=cmd_board)
    p = sub.add_parser('curve'); p.add_argument('batch_id'); p.set_defaults(fn=cmd_curve)
    p = sub.add_parser('read'); p.add_argument('batch_id'); p.add_argument('gravity', type=float)
    p.add_argument('temp', type=float); p.add_argument('--at'); p.set_defaults(fn=cmd_read)
    p = sub.add_parser('finish'); p.add_argument('batch_id'); p.add_argument('fg', type=float); p.set_defaults(fn=cmd_finish)
    p = sub.add_parser('phase'); p.add_argument('phase'); p.set_defaults(fn=cmd_phase)
    p = sub.add_parser('calc'); p.add_argument('og', type=float); p.add_argument('fg', type=float); p.set_defaults(fn=cmd_calc)
    sub.add_parser('doctor').set_defaults(fn=cmd_doctor)
    sub.add_parser('session').set_defaults(fn=cmd_session)
    p = sub.add_parser('feedback'); p.add_argument('text'); p.set_defaults(fn=cmd_feedback)
    args = ap.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()
