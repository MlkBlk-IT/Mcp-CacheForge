import json, os, datetime

p = os.path.join(os.environ['TEMP'], 'icy_live_test.txt')
raw = open(p, 'r', encoding='utf-8', errors='replace').read()
events = []
for line in raw.split('\n'):
    if line.startswith('data:'):
        try:
            events.append(json.loads(line[5:].strip()))
        except Exception as e:
            print('  (ligne partielle:', str(e)[:60], ')')

print('data events:', len(events))
for i, d in enumerate(events):
    m = d['metrics']
    t = datetime.datetime.fromtimestamp(d['ts']).strftime('%H:%M:%S.%f')[:-3]
    saved = m['tokens_in_saved'] + m['tokens_out_saved']
    pts = len(d.get('series', {}).get('points', []))
    models = len(d.get('series', {}).get('models', []))
    print(f'  #{i} ts={t} requests={m["requests"]} hits={m["hits"]} misses={m["misses"]} saved={saved} pts={pts} models={models}')
