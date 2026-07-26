#!/bin/bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
a="$1"; t="$2"
case "$a" in
 gh) u="https://boards-api.greenhouse.io/v1/boards/$t/jobs";;
 ashby) u="https://api.ashbyhq.com/posting-api/job-board/$t?includeCompensation=true";;
 lever) u="https://api.lever.co/v0/postings/$t?mode=json";;
 recruitee) u="https://$t.recruitee.com/api/offers/";;
 workable) u="https://apply.workable.com/api/v1/widget/accounts/$t?details=true";;
 smart) u="https://api.smartrecruiters.com/v1/companies/$t/postings?limit=1";;
 teamtailor) u="https://$t.teamtailor.com/jobs.json";;
esac
c=$(curl -s -m 35 -A "$UA" -o r.$$ -w "%{http_code}" "$u")
n=$(ATS=$a python3 -c "
import sys,json,os,re
try: d=json.load(open('r.$$',encoding='utf8'))
except Exception: print('ERR'); sys.exit()
a=os.environ['ATS']
jobs = d if a=='lever' else (d.get('offers') if a=='recruitee' else (d.get('items') if a=='teamtailor' else d.get('jobs',[])))
if a=='smart': print(d.get('totalFound')); sys.exit()
p=re.compile(r'turkey|türkiye|turkiye|istanbul|i̇stanbul|ankara|izmir',re.I)
def L(j):
    o=[]
    if a=='gh': o=[(j.get('location') or {}).get('name',''),j.get('title','')]+[x.get('name','') for x in (j.get('offices') or [])]
    elif a=='ashby': o=[j.get('location') or '',j.get('title','')]+[ (s.get('location') or '') for s in (j.get('secondaryLocations') or [])]
    elif a=='lever': cc=j.get('categories',{}); o=[cc.get('location') or '',j.get('text','')]+[str(x) for x in (cc.get('allLocations') or [])]
    elif a=='recruitee': o=[j.get('location') or '',j.get('country') or '',j.get('title','')]
    elif a=='teamtailor': o=[json.dumps(j,ensure_ascii=False)]
    return [x for x in o if x]
tr=sum(1 for j in jobs if any(p.search(x) for x in L(j)))
print(f'{len(jobs)}|TR={tr}')
")
rm -f r.$$
echo "$a|$t|http=$c|jobs=$n"
