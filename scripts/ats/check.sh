#!/bin/bash
# usage: check.sh <ats> <token>
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
ats="$1"; tok="$2"
case "$ats" in
  gh) url="https://boards-api.greenhouse.io/v1/boards/$tok/jobs?content=true" ;;
  ashby) url="https://api.ashbyhq.com/posting-api/job-board/$tok?includeCompensation=true" ;;
  lever) url="https://api.lever.co/v0/postings/$tok?mode=json" ;;
  recruitee) url="https://$tok.recruitee.com/api/offers/" ;;
  workable) url="https://apply.workable.com/api/v1/widget/accounts/$tok?details=true" ;;
  smart) url="https://api.smartrecruiters.com/v1/companies/$tok/postings?limit=100" ;;
  teamtailor) url="https://$tok.teamtailor.com/jobs" ;;
  personio) url="https://$tok.jobs.personio.de/search.json" ;;
esac
body=$(curl -s -m 25 -A "$UA" -w "\n__CODE__%{http_code}" "$url")
code=$(printf '%s' "$body" | tail -1 | sed 's/__CODE__//')
json=$(printf '%s' "$body" | sed '$d')
n=""
if [ "$code" = "200" ]; then
  case "$ats" in
    gh) n=$(printf '%s' "$json" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get('jobs',[])))" 2>/dev/null) ;;
    ashby) n=$(printf '%s' "$json" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get('jobs',[])))" 2>/dev/null) ;;
    lever) n=$(printf '%s' "$json" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d))" 2>/dev/null) ;;
    recruitee) n=$(printf '%s' "$json" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get('offers',[])))" 2>/dev/null) ;;
    workable) n=$(printf '%s' "$json" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d.get('jobs',[])))" 2>/dev/null) ;;
    smart) n=$(printf '%s' "$json" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('totalFound',0))" 2>/dev/null) ;;
    personio) n=$(printf '%s' "$json" | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d))" 2>/dev/null) ;;
    *) n="?" ;;
  esac
fi
echo "$ats|$tok|$code|${n:-NA}"
