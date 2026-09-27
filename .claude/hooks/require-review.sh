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
    # the outer command is kept whole across a nested one; $( ) ` ` <( ) leave a $ in its place
    function push(o, mark) {
      sp++; sb[sp] = buf; sr[sp] = redir; ss[sp] = saved; st[sp] = rt; sm[sp] = mark
      sq[sp] = q; so[sp] = o; q = ""; buf = ""; redir = 0; rt = 0
    }
    function pop() {
      flush(); q = sq[sp]; buf = sb[sp] (sm[sp] ? "$" : ""); redir = sr[sp]; saved = ss[sp]; rt = st[sp]
      if (redir && sm[sp]) rt = 1
      sp--
    }
    function hexval(s,   k, v, ch) {
      v = 0
      for (k = 1; k <= length(s); k++) { ch = index("0123456789abcdef", tolower(substr(s, k, 1))) - 1; v = v * 16 + ch }
      return v
    }
    # decoded quotes/backslashes are dropped: they are data, not quoting
    function chr(v) { return (v == 34 || v == 39 || v == 92) ? "" : (v >= 32 && v < 127) ? sprintf("%c", v) : " " }
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
      buf = buf (nx == "\\" || nx == "\047" || nx == "\"" ? "" : " "); return 2
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
          if (c2 == "$(") { push("(", 1); i += 2; continue }
          if (c == "`") { push("`", 1); i++; continue }
          buf = buf (c ~ /[;&|()<> \t]/ ? QS : c); i++; continue
        }
        if (redir && c ~ /[ \t]/) { if (rt) flush_redir_only(); else { i++; continue } }
        if (c == "`") { if (sp > 0 && so[sp] == "`") pop(); else push("`", 1); i++; continue }
        if (c2 == "$(") { push("(", 1); i += 2; continue }
        if (c2 == "$\047") { q = "A"; i += 2; if (redir) rt = 1; continue }
        if (c2 == "<(" || c2 == ">(") { push("(", 1); i += 2; continue }
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
      while (sp > 0) pop()
      if (buf != "") flush()
      if (hd != "") for (k = 1; k <= nb; k++) print body[k]
    }'
}

# gh [global flags [value]] ... ; quotes/backslashes are stripped before matching
flags='([[:space:]]+-[^[:space:]]+([[:space:]]+[^-[:space:]][^[:space:]]*)?)*'
gh_head="^([^[:space:]]*/)?gh${flags}[[:space:]]+"
pr_create_re="${gh_head}pr[[:space:]]+(create|new)([[:space:]]|\$)"
api_re="${gh_head}api[[:space:]]"
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

# --- gh api: only a write to the PR collection (repos/o/r/pulls) or a createPullRequest mutation creates a PR
unq() { printf '%s' "$1" | tr '\002' ' ' | tr -d "\"'\\\\"; }
# expands <raw word> [unquoted-only]: a $ or ` the shell would expand (the segmenter leaves $ for $( ) ` `)
expands() {
  local w=$1 i c q=""
  for ((i = 0; i < ${#w}; i++)); do
    c=${w:i:1}
    if [[ $q == "'" ]]; then [[ $c == "'" ]] && q=""; continue; fi
    case $c in
      "'") [[ $q == '"' ]] || q="'" ;;
      '"') if [[ $q == '"' ]]; then q=""; else q='"'; fi ;;
      '\\') i=$((i + 1)) ;;
      '$' | '`') [[ -n ${2:-} && $q == '"' ]] || return 0 ;;
    esac
  done
  return 1
}
# classify_endpoint <endpoint> <dynamic:true|false> -> graphql | collection <o/r> | unknown | other
classify_endpoint() {
  local e path segs cand worst=other v
  e=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
  [[ $e =~ ^[a-z]+://[^/]*(/.*)?$ ]] && e=${BASH_REMATCH[1]}
  path=${e%%[?#]*}
  while [[ $path == *//* ]]; do path=${path//\/\//\/}; done
  path=${path#/}; path=${path%/}; path=${path#api/v3/}
  [[ $path == api/graphql ]] && path=graphql
  if [[ $path == *\{*,*\}* ]]; then
    # brace expansion builds several endpoints: the riskiest one decides
    while IFS= read -r cand; do
      v=$(classify_endpoint "$cand" "$2")
      case $v in unknown) worst=unknown ;; collection*|graphql) [[ $worst == unknown ]] || worst=$v ;; esac
    done < <(brace_expand "$path")
    printf '%s' "$worst"; return
  fi
  [[ $path == graphql ]] && { printf graphql; return; }
  [[ $path == *[%*[]* || /$path/ == */./* || /$path/ == */../* ]] && { printf unknown; return; }
  if [[ $2 == true || $path == *'$'* || $path == *'`'* ]]; then
    # a variable part can't be checked unless the endpoint is clearly below a PR/issue
    # (repos/o/r/pulls/$N/comments/$ID/replies): its literal last segment rules out the collection
    IFS=/ read -ra segs <<< "$path"
    if (( ${#segs[@]} >= 5 )) && [[ ${segs[0]} == repos && ${segs[3]} =~ ^(pulls|issues)$ \
          && ${segs[${#segs[@]}-1]} =~ ^[a-z_-]+$ && ${segs[${#segs[@]}-1]} != pulls ]]; then
      printf other
    else
      printf unknown
    fi
    return
  fi
  if [[ $path =~ ^repos/([^/]+)/([^/]+)/pulls$ ]]; then printf 'collection %s/%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
  elif [[ $path =~ ^repositories/[^/]+/pulls$ ]]; then printf unknown
  else printf other; fi
}
# api_check <raw segment> <normalized segment>: sets api_verdict to graphql | collection <o/r> | unknown | ""
api_check() {
  local raw=$1 seg=$2 words=() args=() sure=false i j k n w u ch val rawv name
  local positionals=() methods=() fields=() has_input=false endopts=false unquoted_any=false unquoted_before=false
  api_verdict=""
  # quote-aware words (the segmenter keeps quoted spaces as \002), from the gh … api in the raw segment
  read -ra words <<< "$raw"
  for ((i = 0; i < ${#words[@]}; i++)); do
    [[ $(unq "${words[i]}") =~ ^([^[:space:]]*/)?gh$ ]] || continue
    for ((j = i + 1; j < ${#words[@]}; j++)); do
      if [[ $(unq "${words[j]}") == api ]]; then args=("${words[@]:j+1}"); sure=true; break 2; fi
    done
    break
  done
  if ! $sure; then
    # gh api inside a quoted script (bash -c '…'): quoting is lost, every $ counts as unquoted
    [[ $seg =~ ${gh_head}api ]] && read -ra args <<< "${seg:${#BASH_REMATCH[0]}}"
  fi
  n=${#args[@]}
  for ((i = 0; i < n; i++)); do
    w=${args[i]}; u=$(unq "$w")
    # an unquoted expansion ahead of the endpoint can inject a flag that swallows the endpoint word
    if ! $sure || expands "$w" unquoted; then
      [[ $u == *'$'* || $u == *'`'* ]] && { unquoted_any=true; $endopts || [[ $u != -?* ]] || (( ${#positionals[@]} )) || unquoted_before=true; }
    fi
    if $endopts || [[ $u != -?* ]]; then positionals+=("$w"); continue; fi
    [[ $u == -- ]] && { endopts=true; continue; }
    name="" val="" rawv=$w
    if [[ $u == --* ]]; then
      name=${u%%=*}
      [[ $name =~ ^--(method|field|raw-field|header|input|jq|template|hostname|preview|cache)$ ]] || continue
      if [[ $u == *=* ]]; then val=${u#*=}; else i=$((i + 1)); rawv=${args[i]:-}; val=$(unq "$rawv"); fi
    else
      # short flags may be combined (-iXPOST) and take an attached value (-fbody=x, -X=POST)
      for ((k = 1; k < ${#u}; k++)); do
        ch=${u:k:1}
        [[ $ch == [XfFHqtp] ]] || continue
        name=-$ch; val=${u:k+1}; val=${val#=}
        if [ -z "$val" ]; then i=$((i + 1)); rawv=${args[i]:-}; val=$(unq "$rawv"); fi
        break
      done
      [ -n "$name" ] || continue
    fi
    if ! $sure || expands "$rawv" unquoted; then
      [[ $val == *'$'* || $val == *'`'* ]] && { unquoted_any=true; (( ${#positionals[@]} )) || unquoted_before=true; }
    fi
    case $name in
      -X|--method) methods+=("$val") ;;
      -f|-F|--field|--raw-field) fields+=("$rawv") ;;
      --input) has_input=true ;;
    esac
  done
  local write=false m
  if (( ${#methods[@]} )); then
    m=$(printf '%s' "${methods[${#methods[@]}-1]}" | tr '[:lower:]' '[:upper:]')
    [[ $m == GET || $m == HEAD ]] || write=true
  elif (( ${#fields[@]} )) || $has_input; then
    write=true
  fi
  # an unquoted expansion can split into more flags (-X POST, -f …): it may turn anything into a write
  $unquoted_any && write=true
  if (( ${#positionals[@]} == 0 )); then $write && api_verdict=unknown; return 0; fi
  local p cls dyn fw fk
  for p in "${positionals[@]}"; do
    dyn=false; { ! $sure || expands "$p"; } && [[ $p == *'$'* || $p == *'`'* ]] && dyn=true
    cls=$(classify_endpoint "$(unq "$p")" "$dyn")
    case $cls in
      graphql)
        if $sure; then
          [[ $seg =~ [Cc][Rr][Ee][Aa][Tt][Ee][Pp][Uu][Ll][Ll][Rr][Ee][Qq][Uu][Ee][Ss][Tt] ]] || $has_input || $unquoted_any \
            && { api_verdict=graphql; return; }
          for fw in "${fields[@]}"; do
            u=$(unq "$fw"); fk=${u%%=*}
            # the query text must be literal: no expansion, no @file, no computed field name
            if [[ $fk == *'$'* || $fk == *'`'* || ${u#*=} == @* ]] \
               || { [[ $(printf '%s' "$fk" | tr '[:upper:]' '[:lower:]') == query ]] && expands "$fw"; }; then
              api_verdict=graphql; return
            fi
          done
        elif [[ $seg =~ (createPullRequest|--input|=@|\$) ]]; then
          api_verdict=graphql; return
        fi ;;
      unknown) $write && { api_verdict=unknown; return; } ;;
      collection*) $write && [[ $api_verdict != unknown ]] && api_verdict=$cls ;;
      other) $unquoted_before && { api_verdict=unknown; return; } ;;
    esac
  done
  return 0
}

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
  api_verdict=""
  if [[ "$seg" =~ $pr_create_re ]] || [[ "$seg" =~ $dyn_name_re ]] || [[ "$seg" =~ $dyn_sub_re ]] \
     || { [[ "$seg" =~ $api_re ]] && api_check "$raw_seg" "$seg" && [ -n "$api_verdict" ]; }; then
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
case $api_verdict in
  graphql) block "gh api graphql createPullRequest (or a query from --input, @file or a variable) can't be checked. Use gh pr create from the reviewed repo." ;;
  unknown) block "gh api PR target can't be determined. Use gh pr create from the reviewed repo." ;;
esac
if [[ $api_verdict == collection* ]]; then
  want=${api_verdict#collection }
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
