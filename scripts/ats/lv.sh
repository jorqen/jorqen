#!/bin/bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
curl -s -m 25 -A "$UA" "https://api.lever.co/v0/postings/$1?mode=json" | python3 -c "
import sys,json,collections
d=json.load(sys.stdin)
print('== $1  total',len(d))
c=collections.Counter(j['categories'].get('location') for j in d)
for k,v in c.most_common(25): print('  ',v,k)
print('  sample:',d[0]['text'] if d else '')
print('  url:',d[0]['hostedUrl'] if d else '')
"
