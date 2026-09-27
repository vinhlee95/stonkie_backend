#!/usr/bin/env bash
# PreToolUse hook: block `gh pr create` unless the current HEAD has a passing /multi-review.
# Exit 0 = allow, exit 2 = block (stderr is shown to Claude). Fails closed; no bypass flag.
# A workflow guardrail for Claude Code sessions, not a security boundary (the state file is local).
set -uo pipefail

REQUIRED_ANGLES='["functionality","architecture","security","scalability","tests"]'

block() {
  printf 'PR creation blocked by require-review hook: %b\n' "$1" >&2
  exit 2
}

input=$(cat)

if ! command -v jq >/dev/null 2>&1; then
  # without jq the command can't be decoded: let through only input that can't spell a gh command
  # (g…h with only quotes between, any backslash escape, any $ expansion, backticks, globs)
  nojq_re='g[^[:alnum:][:space:]]*h|\\|\$|`|[*?[]'
  [[ "$input" =~ $nojq_re ]] || exit 0
  block "jq is not installed."
fi
command -v git >/dev/null 2>&1 || block "git is not installed."

cmd=$(printf '%s' "$input" | jq -er '.tool_input.command // ""' 2>/dev/null) \
  || block "could not parse hook input."

# Split the command into simple-command segments, one per line, the way a shell would:
# - backslash-newline is removed first (line continuation, also inside double quotes)
# - break on ; & | && || newline outside quotes
# - $( ` ( <( >( open a nested command (also inside double quotes) and ) ` close it again,
#   restoring the surrounding quote state
# - single-quoted text is data; $'...' (ANSI-C) is decoded; a here-string word is data but
#   its $( ) still runs
# - a redirection (>out, 2>&1, <in) and its target are split off so they can't hide the command;
#   the target is data but its $( ) still runs
# - heredoc bodies are data, but with an unquoted delimiter their $( ) and ` ` still run;
#   an unterminated heredoc body is inspected as commands (fail closed)
# Only a segment whose first word is gh counts, so text that merely mentions
# `gh pr create` (commit messages, grep, echo) passes through.
segments() {
  local joined=${1//$'\\\n'/}
  # QS (\002) stands for a space inside quotes, so quoted text stays one word for the generic check
  printf '%s\n' "$joined" | awk -v QS=$'\002' '
    # segments inside ( ), $( ), backticks are marked with \001: a cd there does not move the outer shell
    function emit(s) { print (sp > 0 ? "\001" : "") s }
    function flush() {
      if (redir) { emit(buf); buf = saved; redir = 0; rt = 0 }
      emit(buf); buf = ""
    }
    function push(o) { flush(); sp++; sq[sp] = q; so[sp] = o; q = "" }
    function pop() { flush(); q = sq[sp]; sp-- }
    function hexval(s,   k, v, ch) {
      v = 0
      for (k = 1; k <= length(s); k++) { ch = index("0123456789abcdef", tolower(substr(s, k, 1))) - 1; v = v * 16 + ch }
      return v
    }
    function chr(v) { return (v >= 32 && v < 127) ? sprintf("%c", v) : " " }
    function startredir() {
      # a fd number right before the operator (2>) belongs to the redirection
      if (match(buf, /(^|[ \t])[0-9]+$/)) buf = substr(buf, 1, RSTART - 1 + (RLENGTH > 0 && substr(buf, RSTART, 1) ~ /[ \t]/ ? 1 : 0))
      saved = buf; buf = "REDIR "; redir = 1; rt = 0
    }
    # one character of an ANSI-C $'...' string starting at i; returns chars consumed
    function ansi(line, i,   c, nx, m, h) {
      c = substr(line, i, 1)
      if (c != "\\") { buf = buf (c ~ /[;&|`()<>$ \t]/ ? QS : c); return 1 }
      nx = substr(line, i + 1, 1)
      if (nx == "x" && match(substr(line, i + 2), /^[0-9A-Fa-f][0-9A-Fa-f]?/)) { buf = buf chr(hexval(substr(line, i + 2, RLENGTH))); return 2 + RLENGTH }
      if ((nx == "u" || nx == "U") && match(substr(line, i + 2), /^[0-9A-Fa-f]+/)) {
        m = (nx == "u" ? 4 : 8); h = substr(line, i + 2, (RLENGTH < m ? RLENGTH : m))
        buf = buf chr(hexval(h)); return 2 + length(h)
      }
      if (match(substr(line, i + 1), /^[0-7][0-7]?[0-7]?/)) {
        h = substr(line, i + 1, RLENGTH); m = 0
        for (k = 1; k <= length(h); k++) m = m * 8 + substr(h, k, 1)
        buf = buf chr(m); return 1 + RLENGTH
      }
      buf = buf (nx == "\\" || nx == "\047" || nx == "\"" ? nx : " "); return 2
    }
    hd != "" {
      t = $0; gsub(/^[ \t]+|[ \t]+$/, "", t)
      if (t == hd) { hd = ""; nb = 0; next }
      body[++nb] = $0
      if (hq) next
      # unquoted heredoc body: text is data, $( ) and ` ` are commands
      q = "\""; buf = "HEREDOC "; line = $0 "\"" ; n = length(line); i = 1
    }
    hd == "" { line = $0; n = length(line); i = 1 }
    {
      while (i <= n) {
        c = substr(line, i, 1); c2 = substr(line, i, 2)
        if (q == "\047") { if (c == "\047") q = ""; buf = buf (c == "\047" ? c : (c ~ /[;&|`()<>$ \t]/ ? QS : c)); i++; continue }
        if (q == "A") { if (c == "\047") { q = ""; i++; continue } i += ansi(line, i); continue }
        if (q == "\"") {
          if (c == "\\") { buf = buf QS; i += 2; continue }
          if (c == "\"") { q = ""; buf = buf c; i++; continue }
          if (c2 == "$(") { push("("); i += 2; continue }
          if (c == "`") { push("`"); i++; continue }
          buf = buf (c ~ /[;&|()<> \t]/ ? QS : c); i++; continue
        }
        if (redir && c ~ /[ \t]/) { if (rt) flush_redir_only(); else { i++; continue } }
        if (c == "`") { if (sp > 0 && so[sp] == "`") pop(); else push("`"); i++; continue }
        if (c2 == "$(") { push("("); i += 2; continue }
        if (c2 == "$\047") { q = "A"; i += 2; if (redir) rt = 1; continue }
        if (c2 == "<(" || c2 == ">(") { push("("); i += 2; continue }
        if (c == "(") { push("("); i++; continue }
        if (c == ")") { if (sp > 0 && so[sp] == "(") pop(); else flush(); i++; continue }
        if (substr(line, i, 3) == "<<<") { flush(); buf = "HERESTRING "; i += 3; continue }
        if (c2 == "<<" && match(substr(line, i + 2), /^-?[ \t]*["\047]?[A-Za-z_][A-Za-z0-9_]*["\047]?/)) {
          d = substr(line, i + 2 + RSTART - 1, RLENGTH); hq = (d ~ /["\047]/)
          gsub(/^-?[ \t]*["\047]?|["\047]$/, "", d)
          hd = d; nb = 0; i += 2 + RLENGTH; continue
        }
        if (c2 == "&>" || c ~ /[<>]/) {
          if (redir) flush_redir_only()
          startredir()
          i += (c2 == "&>" ? 2 : 1)
          while (substr(line, i, 1) ~ /[<>&|]/) i++
          continue
        }
        if (c == "\047" || c == "\"") { q = c; buf = buf c; i++; if (redir) rt = 1; continue }
        if (c == "\\") { buf = buf substr(line, i, 2); i += 2; if (redir) rt = 1; continue }
        if (c ~ /[;&|]/) { flush(); i++; continue }
        if (redir && c !~ /[ \t]/) rt = 1
        buf = buf c; i++
      }
      if (hd != "" && !hq && buf ~ /^HEREDOC /) { emit(buf); buf = ""; q = ""; next }
      if (q == "") flush(); else buf = buf QS
    }
    function flush_redir_only() { emit(buf); buf = saved; redir = 0; rt = 0 }
    END {
      if (buf != "") flush()
      if (hd != "") for (k = 1; k <= nb; k++) print body[k]
    }'
}

# gh [global flags [value]] ... ; quotes/backslashes are stripped before matching
flags='([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*'
gh_head="^([^[:space:]]*/)?gh${flags}[[:space:]]+"
pr_create_re="${gh_head}pr[[:space:]]+(create|new)([[:space:]]|\$)"
api_re="${gh_head}api[[:space:]]"
# only the create endpoint; /pulls/<n>/... (comments, reviews) is not PR creation
pulls_re='/pulls([?[:space:]]|$)'
api_write_re='(-X|--method)[[:space:]=]*([Pp][Oo][Ss][Tt]|\$)|[[:space:]](-f|-F|--field|--raw-field|--input)'
# an endpoint built from a variable can't be checked: treat a write to it as PR creation
api_dynamic_re="${gh_head}api[[:space:]]+([^[:space:]]+[[:space:]]+)*[^-[:space:]]*\\$"
# commands/keywords (incl. any shell's -c: sh/bash/dash/ksh/zsh/fish/busybox sh) that run (part of)
# the rest of the segment as a command; after one, the
# command is the first gh word that follows, whatever options/operands sit in between
wrapper_re='^(([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*)|([^[:space:]]*/)?(command|sudo|env|exec|eval|time|nohup|nice|timeout|xargs)|if|then|else|elif|do|while|until|!|\{|([^[:space:]]*/)?(busybox[[:space:]]+)?([a-z]*sh|fish)([[:space:]]+-[^[:space:]]+)*[[:space:]]+-[A-Za-z]*c[A-Za-z]*)[[:space:]]+'
wrapped_gh_re='(^|[[:space:]])(([^[:space:]]*/)?gh([[:space:]].*)?)$'
cd_re='^(cd|pushd)([[:space:]]+(-[LPe@]+[[:space:]]+|--[[:space:]]+)*(.*[^[:space:]]))?[[:space:]]*$'
# a command name built from an expansion ($G, g${X}h, $(echo gh), leftover "pr create") can't be checked
dyn_name_re='^([^[:space:]]*\$[^[:space:]]*[[:space:]]+([^[:space:]]+[[:space:]]+)*)?pr[[:space:]]+(create|new)([[:space:]]|$)'
# gh api graphql can create PRs; a mutation, --input file, @file field or $ expansion can't be inspected
# an expansion inside the subcommand (gh pr cr${X}eate, gh ${SUB} …) can't be checked
dyn_sub_re="${gh_head}([^[:space:]]*\\$|pr[[:space:]]+[^[:space:]]*\\$)"
# any unquoted gh … pr create|new word sequence behind an unknown executor (setsid gh …)
# a shell -c anywhere in the segment (behind an unknown executor: setsid bash -c '…') runs its script
any_shell_c_re='(^|[[:space:]])([^[:space:]]*/)?(busybox[[:space:]]+)?([a-z]*sh|fish)([[:space:]]+-[^[:space:]]+)*[[:space:]]+-[A-Za-z]*c[A-Za-z]*[[:space:]]+(.*)$'
# running a script file (sh ./x.sh, ./x.sh, source x, . x): look inside it
script_re='^(([^[:space:]]*/)?([a-z]*sh|fish|python[0-9.]*|node|ruby|perl|php|deno([[:space:]]+run)?|bun([[:space:]]+run)?|tsx|ts-node|npx[[:space:]]+(tsx|ts-node))([[:space:]]+-[^[:space:]]+)*[[:space:]]+|source[[:space:]]+|\.[[:space:]]+)?([^[:space:]-][^[:space:]]*)'
inline_code_re='^([^[:space:]]*/)?(python[0-9.]*|node|ruby|perl|php|deno[[:space:]]+eval|bun[[:space:]]+-e)([[:space:]]+-[^[:space:]]+)*[[:space:]]+-[A-Za-z]*[cepr]([[:space:]]|$)'
script_creates_pr() {  # script_creates_pr <path relative to cwd>
  local f=$1
  [[ $f == /* ]] || f="${cd_target:-$cwd}/$f"
  [ -f "$f" ] && [ -r "$f" ] || return 1
  grep -qE '(^|[^[:alnum:]_-])gh([^[:alnum:]_-]|$)' "$f" \
    && grep -qE 'pr["'"'"'[:space:],]+(create|new)|/pulls|createPullRequest|mutation' "$f"
}
# glob patterns in the first command words (g? pr create) expand before running: match them
unglob_words() {
  local out="" word k=0 cand
  for word in $1; do
    if (( k < 4 )) && [[ $word == *[*?[]* ]]; then
      for cand in gh pr create new api; do
        # shellcheck disable=SC2053
        if [[ $cand == $word || ${word##*/} != "$word" && $cand == ${word##*/} ]]; then word=$cand; break; fi
      done
    fi
    out+="$word "; k=$((k + 1))
  done
  printf '%s' "${out% }"
}
generic_re='(^|[[:space:]])(([^[:space:]"'"'"']*/)?gh([[:space:]]+-[^[:space:]"'"'"']+([[:space:]]+[^-[:space:]"'"'"'][^[:space:]"'"'"']*)?)*[[:space:]]+pr[[:space:]]+(create|new)([[:space:]].*)?)$'
graphql_create_re="${gh_head}api[[:space:]]+graphql([[:space:]]|\$).*(createPullRequest|mutation|--input|=@|\\$)"

# Bash brace expansion ({gh,pr,create}, g{h,}, nested {{a,b},c}) builds words before a command runs:
# expand it the same way so the result is what gets matched. Prints one expansion per line.
brace_expand() {
  local w=$1 n=${#1} i c depth=0 start=-1 end=-1 comma=0
  for ((i = 0; i < n; i++)); do
    c=${w:i:1}
    if [[ $c == "{" ]]; then
      (( depth == 0 )) && { start=$i; comma=0; }
      depth=$((depth + 1))
    elif [[ $c == "}" && $depth -gt 0 ]]; then
      depth=$((depth - 1))
      if (( depth == 0 )); then
        if (( comma )); then end=$i; break; fi
        start=-1
      fi
    elif [[ $c == "," && $depth -eq 1 ]]; then
      comma=1
    fi
  done
  if (( end < 0 )); then printf '%s\n' "$w"; return; fi
  local pre=${w:0:start} body=${w:start+1:end-start-1} post=${w:end+1} alt="" d=0
  for ((i = 0; i < ${#body}; i++)); do
    c=${body:i:1}
    [[ $c == "{" ]] && d=$((d + 1))
    [[ $c == "}" ]] && d=$((d - 1))
    if [[ $c == "," && $d -eq 0 ]]; then brace_expand "$pre$alt$post"; alt=""; else alt+=$c; fi
  done
  brace_expand "$pre$alt$post"
}
expand_segment() {
  local out="" word
  for word in $1; do
    if [[ $word == *\{*,*\}* ]]; then word=$(brace_expand "$word" | tr '\n' ' '); word=${word% }; fi
    out+="$word "
  done
  printf '%s' "${out% }"
}

cwd=$(printf '%s' "$input" | jq -r '.cwd // empty')
[ -n "$cwd" ] || cwd="${CLAUDE_PROJECT_DIR:-$PWD}"
is_pr_create=false
pr_seg=""
cd_target=""
cd_unresolved=""
# gh reads its target repo from GH_REPO (environment, export, VAR=… prefix, env VAR=…)
gh_repo_override="${GH_REPO:-}"
gh_host_override="${GH_HOST:-}"
head_changed=""
while IFS= read -r seg; do
  nested=false
  [[ "$seg" == $'\001'* ]] && { nested=true; seg="${seg#$'\001'}"; }
  raw_seg="$seg"
  seg=$(printf '%s' "$seg" | tr '\002' ' ' | tr -d "\"'\\\\")
  seg="${seg#"${seg%%[![:space:]]*}"}"
  [[ "$seg" == *\{*,*\}* ]] && seg=$(set -f; expand_segment "$seg")
  [[ "$seg" =~ (^|[[:space:]])GH_REPO=([^[:space:]]*) ]] && gh_repo_override="${BASH_REMATCH[2]:-__empty__}"
  [[ "$seg" =~ (^|[[:space:]])GH_HOST=([^[:space:]]*) ]] && gh_host_override="${BASH_REMATCH[2]}"
  # env -C/--chdir and sudo -D/--chdir run the command in another directory
  [[ "$seg" =~ ^(([^[:space:]]*/)?(env|sudo))[[:space:]].*(-C|--chdir|-D)([[:space:]=]|[^[:space:]]) ]] && cd_unresolved="a wrapper changes directory"
  # git commands that move HEAD or switch branch before gh in the same command
  [[ "$seg" =~ ^([^[:space:]]*/)?git([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*[[:space:]]+(commit|switch|checkout|reset|rebase|merge|pull|cherry-pick|revert|am|stash)([[:space:]]|$) ]] \
    && head_changed="git ${BASH_REMATCH[4]}"
  [[ "$seg" == *[*?[]* ]] && seg=$(set -f; unglob_words "$seg")
  wrapped=false
  while [[ "$seg" =~ $wrapper_re ]]; do seg="${seg:${#BASH_REMATCH[0]}}"; wrapped=true; done
  if ! [[ "$seg" =~ ^([^[:space:]]*/)?gh[[:space:]] ]] && [[ "$seg" =~ $any_shell_c_re ]]; then
    seg="${BASH_REMATCH[6]}"; wrapped=true
    while [[ "$seg" =~ $wrapper_re ]]; do seg="${seg:${#BASH_REMATCH[0]}}"; done
  fi
  if $wrapped && ! [[ "$seg" =~ ^([^[:space:]]*/)?gh[[:space:]] ]] && [[ "$seg" =~ $wrapped_gh_re ]]; then
    # a directory change inside the wrapped script (bash -c 'cd x && gh …') can't be followed
    gh_part="${BASH_REMATCH[2]}"
    [[ "${seg%"$gh_part"}" =~ (^|[[:space:]])(cd|pushd|popd)([[:space:]]|$) ]] && cd_unresolved="a wrapped command changes directory"
    seg="$gh_part"
  fi
  if [[ "$seg" =~ ^popd([[:space:]]|$) ]]; then cd_unresolved="popd"; continue; fi
  if [[ "$seg" =~ $cd_re ]]; then
    if $nested; then cd_unresolved="cd inside a subshell"
    elif [ -n "$cd_target" ]; then cd_unresolved="more than one cd"
    else cd_target="${BASH_REMATCH[4]:-$HOME}"; fi
    continue
  fi
  if ! [[ "$seg" =~ ^([^[:space:]]*/)?gh[[:space:]] ]] && [[ "$raw_seg" =~ $generic_re ]]; then
    seg=$(printf '%s' "${BASH_REMATCH[2]}" | tr -d "\"'\\\\")
  fi
  # script file: resolve the path from the quote-aware segment so "create pr.py" stays one path
  qseg=$(printf '%s' "$raw_seg" | tr -d "\"'\\\\")
  if [[ "$qseg" =~ $script_re ]] && [[ -n "${BASH_REMATCH[1]}" || "${BASH_REMATCH[8]}" == */* ]]; then
    spath=$(printf '%s' "${BASH_REMATCH[8]}" | tr '\002' ' ')
    script_creates_pr "$spath" && block "script $spath contains gh PR-creation commands. Run gh pr create directly after /multi-review."
  fi
  # inline interpreter code (python -c, node -e/-p, ruby/perl -e, php -r)
  if [[ "$seg" =~ $inline_code_re ]] && [[ "$seg" =~ (^|[^[:alnum:]_-])gh([^[:alnum:]_-]|$) ]] \
     && [[ "$seg" =~ pr[[:space:],]+(create|new)|/pulls|createPullRequest|mutation ]]; then
    block "inline interpreter code contains gh PR-creation commands. Run gh pr create directly after /multi-review."
  fi
  if [[ "$seg" =~ $pr_create_re ]] || [[ "$seg" =~ $dyn_name_re ]] || [[ "$seg" =~ $dyn_sub_re ]] \
     || [[ "$seg" =~ $graphql_create_re ]] \
     || { [[ "$seg" =~ $api_re ]] && [[ "$seg" =~ $api_write_re ]] \
          && { [[ "$seg" =~ $pulls_re ]] || [[ "$seg" =~ $api_dynamic_re ]]; }; }; then
    is_pr_create=true
    pr_seg="$seg"
    break
  fi
done < <(segments "$cmd")
$is_pr_create || exit 0

[ -n "$head_changed" ] && block "\`$head_changed\` runs before gh pr create in the same command, so HEAD may change after this check. Run it separately, then /multi-review, then gh pr create on its own."
[ -n "$cd_unresolved" ] && block "cannot tell which directory gh runs in ($cd_unresolved). Run gh pr create from inside the reviewed repo."
# `cd <dir> && gh pr create` creates the PR from <dir>: judge that repo
if [ -n "$cd_target" ]; then
  [[ "$cd_target" == "-" || "$cd_target" == *'$'* || "$cd_target" == *'`'* ]] \
    && block "cannot resolve the cd target ($cd_target). Run gh pr create from inside the reviewed repo."
  case "$cd_target" in
    "~"*) cwd="$HOME${cd_target#\~}" ;;
    /*) cwd="$cd_target" ;;
    *) cwd="$cwd/$cd_target" ;;
  esac
fi

top=$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null) || block "$cwd is not inside a git repository."
sha=$(git -C "$top" rev-parse HEAD 2>/dev/null) || block "could not resolve HEAD in $top."

# the PR must target the reviewed repo, branch and base
origin_repo() {
  git -C "$top" remote get-url origin 2>/dev/null | tr '[:upper:]' '[:lower:]' | sed -E 's#\.git$##; s#^.*[:/]([^/:]+/[^/]+)$#\1#'
}
check_head() {  # check_head <owner:branch | branch>
  local owner="" branch="$1" current
  if [[ "$1" == *:* ]]; then owner=$(printf '%s' "${1%%:*}" | tr '[:upper:]' '[:lower:]'); branch="${1##*:}"; fi
  current=$(git -C "$top" rev-parse --abbrev-ref HEAD 2>/dev/null)
  [ "$branch" = "$current" ] || block "head $branch is not the reviewed branch ($current). Check it out and run /multi-review there."
  if [ -n "$owner" ]; then
    local have; have=$(origin_repo)
    [ "$owner" = "${have%%/*}" ] || block "head owner $owner is not this repository's owner (${have%%/*}). Only the reviewed branch can be proposed."
  fi
}
check_base() {  # check_base <branch>
  local def
  def=$(git -C "$top" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null); def="${def#origin/}"
  [ "$1" = "${def:-main}" ] || block "base $1 differs from the reviewed base (${def:-main}); /multi-review reviews origin/${def:-main}...HEAD."
}
if [[ "$pr_seg" =~ $graphql_create_re ]]; then
  block "gh api graphql writes (mutations, --input, @file or variable queries) can't be checked. Use gh pr create from the reviewed repo."
fi
if [[ "$pr_seg" =~ $api_re ]]; then
  [[ "$pr_seg" =~ repos/([^/[:space:]]+)/([^/[:space:]?]+)/pulls ]] \
    || block "gh api PR target can't be determined. Use gh pr create from the reviewed repo."
  want=$(printf '%s/%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" | tr '[:upper:]' '[:lower:]')
  [[ "$want" == *'$'* ]] && block "gh api PR target is dynamic ($want). Use gh pr create from the reviewed repo."
  have=$(origin_repo)
  [ -n "$have" ] && [ "$want" = "$have" ] || block "gh api targets $want, not this repository's origin (${have:-none})."
  [[ "$pr_seg" =~ [[:space:]](-f|-F|--field|--raw-field)([[:space:]]*|=)head=([^[:space:]]+) ]] && check_head "${BASH_REMATCH[3]}"
  [[ "$pr_seg" =~ [[:space:]](-f|-F|--field|--raw-field)([[:space:]]*|=)base=([^[:space:]]+) ]] && check_base "${BASH_REMATCH[3]}"
fi
if [ -n "$gh_host_override" ]; then
  origin_host=$(git -C "$top" remote get-url origin 2>/dev/null | tr '[:upper:]' '[:lower:]' | sed -E 's#^[a-z]+://([^@/]*@)?##; s#^[^@]*@##; s#[:/].*$##')
  [ "$(printf '%s' "$gh_host_override" | tr '[:upper:]' '[:lower:]')" = "${origin_host:-github.com}" ] \
    || block "GH_HOST=$gh_host_override is not origin's host (${origin_host:-none}). Unset it or create the PR from the reviewed repo."
fi
if [ -n "$gh_repo_override" ] && [ "$gh_repo_override" != "__empty__" ]; then
  want=$(printf '%s' "$gh_repo_override" | tr '[:upper:]' '[:lower:]' | awk -F/ '{print $(NF-1) "/" $NF}')
  have=$(origin_repo)
  [ -n "$have" ] && [ "$want" = "$have" ] || block "GH_REPO=$gh_repo_override is not this repository's origin (${have:-none}). Unset it or create the PR from the reviewed repo."
fi
if [[ "$pr_seg" =~ (^|[[:space:]])(-R[[:space:]]*|--repo([[:space:]]+|=))([^[:space:]]+) ]]; then
  want=$(printf '%s' "${BASH_REMATCH[4]}" | tr '[:upper:]' '[:lower:]' | awk -F/ '{print $(NF-1) "/" $NF}')
  have=$(origin_repo)
  [ -n "$have" ] && [ "$want" = "$have" ] || block "--repo $want is not this repository's origin (${have:-none}). Create the PR from the reviewed repo."
fi
[[ "$pr_seg" =~ (^|[[:space:]])(-H[[:space:]]*|--head([[:space:]]+|=))([^[:space:]]+) ]] && check_head "${BASH_REMATCH[4]}"
[[ "$pr_seg" =~ (^|[[:space:]])(-B[[:space:]]*|--base([[:space:]]+|=))([^[:space:]]+) ]] && check_base "${BASH_REMATCH[4]}"

if [ -n "$(git -C "$top" status --porcelain --untracked-files=no)" ]; then
  block "uncommitted changes to tracked files. Commit them, then run /multi-review so the review covers exactly what will be pushed."
fi

state="$top/.claude/review-state/$sha.json"
[ -f "$state" ] || block "no review found for HEAD $sha. Run /multi-review first."

jq -e 'type == "object"' "$state" >/dev/null 2>&1 || block "review state $state is malformed. Re-run /multi-review."
jq -e --arg sha "$sha" '.sha == $sha' "$state" >/dev/null 2>&1 || block "review state $state was not written for HEAD $sha. Re-run /multi-review."

bad_angles=$(jq -r --argjson req "$REQUIRED_ANGLES" '
  $req[] as $a | select((.angles // {})[$a] != "ok") | "  - angle \($a): \((.angles // {})[$a] // "missing")"
' "$state" 2>/dev/null) || block "review state $state is malformed. Re-run /multi-review."

open=$(jq -r '
  ((.waivers // []) | map(.id)) as $waived
  | (.findings // [])[]
  | select(.severity == "critical" or .severity == "high")
  | select(.id as $id | ($waived | index($id)) | not)
  | "  - [\(.severity)] \(.id): \(.title) (\(.file):\(.line // "-"))"
' "$state" 2>/dev/null) || block "review state $state is malformed. Re-run /multi-review."

if [ -n "$bad_angles" ] || [ -n "$open" ]; then
  msg="review for HEAD $sha did not pass."
  [ -n "$bad_angles" ] && msg="$msg\nAngles not reviewed:\n$bad_angles"
  [ -n "$open" ] && msg="$msg\nOpen critical/high findings:\n$open"
  msg="$msg\nFix, commit, and re-run /multi-review. Only the user may waive a finding."
  block "$msg"
fi

exit 0
