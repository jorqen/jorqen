#!/bin/bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
u="$1"
out=$(curl -sL -m 30 -A "$UA" -H "Accept-Language: en-US,en;q=0.9" "$u" 2>/dev/null)
code=$(curl -sL -m 20 -A "$UA" -o /dev/null -w "%{http_code}" "$u" 2>/dev/null)
hits=$(printf '%s' "$out" | grep -oiE '(boards\.greenhouse\.io/[a-z0-9_-]+|job-boards\.greenhouse\.io/[a-z0-9_-]+|greenhouse\.io/embed/job_board[^"'"'"']*|jobs\.lever\.co/[a-z0-9_-]+|jobs\.ashbyhq\.com/[a-z0-9_.-]+|[a-z0-9-]+\.recruitee\.com|apply\.workable\.com/[a-z0-9_-]+|[a-z0-9-]+\.workable\.com|careers\.smartrecruiters\.com/[A-Za-z0-9_-]+|jobs\.smartrecruiters\.com/[A-Za-z0-9_-]+|[a-z0-9-]+\.teamtailor\.com|[a-z0-9-]+\.jobs\.personio\.(de|com)|[a-z0-9-]+\.bamboohr\.com|[a-z0-9-]+\.applytojob\.com|[a-z0-9-]+\.breezy\.hr|[a-z0-9-]+\.freshteam\.com|myworkdayjobs\.com/[A-Za-z0-9_-]+|[a-z0-9-]+\.zohorecruit\.[a-z]+|kariyer\.net|peopleforce\.io|[a-z0-9-]+\.pinpointhq\.com|jobs\.jobvite\.com/[a-z0-9_-]+|[a-z0-9-]+\.talentlyft\.com|[a-z0-9-]+\.hire\.trakstar\.com|[a-z0-9-]+\.rippling\.com|jobs\.gohire\.io|[a-z0-9-]+\.factorialhr\.com|[a-z0-9-]+\.humaans\.io|[a-z0-9-]+\.hibob\.com|smartrecruiters\.com/[A-Za-z0-9_-]+)' | sort -u | head -12)
echo "### $u [$code]"
[ -n "$hits" ] && printf '%s\n' "$hits" || echo "   (no ats markers)"
