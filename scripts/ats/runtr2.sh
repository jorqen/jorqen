#!/bin/bash
while read -r a t; do [ -z "$a" ] && continue; ./tr2.sh "$a" "$t" & done
wait
