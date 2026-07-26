#!/bin/bash
# usage: tr.sh <ats> <token>  -> prints token, total, turkey-matching locations
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
ats="$1"; tok="$2"
case "$ats" in
  gh) url="https://boards-api.greenhouse.io/v1/boards/$tok/jobs" ;;
  ashby) url="https://api.ashbyhq.com/posting-api/job-board/$tok" ;;
  lever) url="https://api.lever.co/v0/postings/$tok?mode=json" ;;
  recruitee) url="https://$tok.recruitee.com/api/offers/" ;;
  workable) url="https://apply.workable.com/api/v1/widget/accounts/$tok?details=true" ;;
esac
curl -s -m 30 -A "$UA" "$url" 2>/dev/null | ATS="$ats" TOK="$tok" python3 -c "
import sys,json,os,re
ats=os.environ['ATS']; tok=os.environ['TOK']
try: d=json.load(sys.stdin)
except Exception: sys.exit()
if ats in ('gh','ashby','workable'): jobs=d.get('jobs',[])
elif ats=='lever': jobs=d
elif ats=='recruitee': jobs=d.get('offers',[])
else: jobs=[]
def loc(j):
    if ats=='gh': return (j.get('location') or {}).get('name','')
    if ats=='ashby': return j.get('location','') or ''
    if ats=='lever': return j.get('categories',{}).get('location','') or ''
    if ats=='workable': return (j.get('location') or {}).get('city','') or str(j.get('location') or '')
    if ats=='recruitee': return (j.get('location') or '')+' '+(j.get('country') or '')
    return ''
pat=re.compile(r'turk|türk|istanbul|i̇stanbul|ankara|izmir|\bTR\b',re.I)
hits=[(j.get('title') or j.get('text') or j.get('name'), loc(j)) for j in jobs if pat.search(loc(j) or '')]
if jobs: print(f'{ats}|{tok}|total={len(jobs)}|TRhits={len(hits)}', '||', '; '.join(sorted(set(h[1] for h in hits))[:6]))
"
