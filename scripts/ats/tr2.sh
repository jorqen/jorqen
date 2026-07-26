#!/bin/bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
ats="$1"; tok="$2"
case "$ats" in
  gh) url="https://boards-api.greenhouse.io/v1/boards/$tok/jobs" ;;
  ashby) url="https://api.ashbyhq.com/posting-api/job-board/$tok" ;;
  lever) url="https://api.lever.co/v0/postings/$tok?mode=json" ;;
esac
curl -s -m 35 -A "$UA" "$url" 2>/dev/null | ATS="$ats" TOK="$tok" python3 -c "
import sys,json,os,re
ats=os.environ['ATS']; tok=os.environ['TOK']
try: d=json.load(sys.stdin)
except Exception: sys.exit()
jobs = d if ats=='lever' else d.get('jobs',[])
if not jobs: sys.exit()
p=re.compile(r'turkey|türkiye|turkiye|istanbul|i̇stanbul|ankara|izmir|i̇zmir',re.I)
def locs(j):
    o=[]
    if ats=='gh':
        o.append((j.get('location') or {}).get('name',''))
        for off in j.get('offices') or []: o.append(off.get('name',''))
        o.append(j.get('title',''))
    elif ats=='ashby':
        o.append(j.get('location','') or '')
        for s in j.get('secondaryLocations') or []: o.append(s.get('location','') or '')
        o.append(j.get('title',''))
    elif ats=='lever':
        c=j.get('categories',{}); o.append(c.get('location','') or '')
        o+= [str(x) for x in (c.get('allLocations') or [])]
        o.append(j.get('text',''))
    return [x for x in o if x]
hits=[j for j in jobs if any(p.search(x) for x in locs(j))]
if hits: print(f'{ats}|{tok}|total={len(jobs)}|TR={len(hits)} ::', '; '.join(sorted({x for j in hits for x in locs(j) if p.search(x)})[:5]))
"
