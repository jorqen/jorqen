#!/bin/bash
while read -r ats tok; do
  [ -z "$ats" ] && continue
  ./check.sh "$ats" "$tok" &
done
wait
