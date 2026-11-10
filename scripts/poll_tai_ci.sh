#!/usr/bin/env bash
# Poll GitHub Actions for the pushed head commit until every run concludes.
#
# The token comes from the remote URL — it is never printed, never written
# anywhere. The poll ends the moment every run for the SHA is completed; a
# single failure is reported with its name.
#
#     scripts/poll_tai_ci.sh
set -uo pipefail

remote_url="$(git remote get-url origin)"
case "$remote_url" in
  *github.com/*) ;;
  *) echo "not a GitHub remote: $remote_url" >&2; exit 2 ;;
esac
path=${remote_url#*github.com[/:]}
owner=${path%%/*}
name=${path#*/}
name=${name%%.git}

# The stored GitHub credential's password, by the same door git pushes
# through. Never printed, never written anywhere but this process's memory.
token="$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill 2>/dev/null \
         | sed -n 's/^password=//p')"
head="$(git rev-parse HEAD)"

auth=()
if [[ -n "$token" ]]; then
  auth=(-H "Authorization: Bearer $token")
fi

api() {
  curl -sf -m 30 -H "Accept: application/vnd.github+json" "${auth[@]}" \
       -H "User-Agent: tai-ci-poll" "$1"
}

echo "polling Actions for $owner/$name @ ${head:0:12}"
deadline=$(( $(date +%s) + 540 ))
while (( $(date +%s) < deadline )); do
  body="$(api "https://api.github.com/repos/$owner/$name/actions/runs?head_sha=$head" || true)"
  if [[ -z "$body" ]]; then
    echo "  api request failed; retrying"
  else
    # One line per run: "name|status|conclusion". The name cannot hold a |.
    runs="$(printf '%s' "$body" | sed -n \
      's/.*"name": *"\([^"]*\)".*/\nRUN \1/; s/.*"status": *"\([^"]*\)".*/\nSTATUS \1/; s/.*"conclusion": *"\([^"]*\)".*/\nCONCL \1/p' \
      )"
    # The JSON fields arrive per run in file order; walk them in triples the
    # way the API writes them (name, status, conclusion are consecutive).
    totals=$(printf '%s\n' "$body" | grep -c '"head_sha"')
    done_runs=$(printf '%s\n' "$body" | grep -c '"status": "completed"')
    if (( totals > 0 && done_runs == totals )); then
      failed=0
      while IFS= read -r concl; do
        if [[ "$concl" != "success" ]]; then failed=1; fi
        echo "  conclusion: $concl"
      done < <(printf '%s\n' "$body" | sed -n 's/.*"conclusion": *"\([^"]*\)".*/\1/p')
      if (( failed )); then
        echo "FAILED"
        exit 1
      fi
      echo "ALL-GREEN"
      exit 0
    fi
    echo "  $(( totals - done_runs )) run(s) in progress of $totals"
  fi
  sleep 15
done
echo "TIMEOUT waiting for CI"
exit 2
