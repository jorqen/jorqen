#!/bin/bash
while read -r ats tok; do [ -z "$ats" ] && continue; ./tr.sh "$ats" "$tok" & done
wait
