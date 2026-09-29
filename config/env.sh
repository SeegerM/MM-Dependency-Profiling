# Shared shell environment. Source it, never execute it:
#   source config/env.sh
#
# Loads config/paths.env, then the gitignored config/paths.local.env. An environment variable of
# the same name wins over both, exactly as in experiments/lib/paths.py, so a one-off run can say
#   WORK_ROOT=/scratch/mm ./run.sh verify
# Relative values are resolved against the repository root, so a path means the same thing from
# every working directory.
#
# It also hides the two differences between Linux/macOS and Windows Git Bash that the Java runners
# care about: the classpath separator, and that java.exe needs native paths (F:/x, not /f/x).

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) CPSEP=';'; native_pwd() { pwd -W; } ;;
  *)                    CPSEP=':'; native_pwd() { pwd; } ;;
esac
export CPSEP

# Absolute native path of an existing directory.
native() { (cd "$1" && native_pwd); }

# Native form of any path, existing or not (F:/x on Windows, unchanged elsewhere).
mm_path() {
  if [ "$CPSEP" = ";" ] && command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

# Joins its arguments into one classpath, skipping empty ones.
cpjoin() {
  local out="" part
  for part in "$@"; do
    [ -n "$part" ] || continue
    out="${out:+$out$CPSEP}$part"
  done
  printf '%s' "$out"
}

MM_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && native_pwd)"
export MM_REPO

_mm_load_env() {
  local file line key value
  declare -A values=()
  local order=()
  for file in "$MM_REPO/config/paths.env" "$MM_REPO/config/paths.local.env"; do
    [ -f "$file" ] || continue
    while IFS= read -r line || [ -n "$line" ]; do
      line="${line%$'\r'}"
      line="${line#"${line%%[![:space:]]*}"}"
      case "$line" in ''|'#'*) continue ;; *=*) ;; *) continue ;; esac
      key="${line%%=*}"; key="${key%"${key##*[![:space:]]}"}"
      value="${line#*=}"; value="${value#"${value%%[![:space:]]*}"}"
      [ -n "${values[$key]+x}" ] || order+=("$key")
      values[$key]="$value"
    done < "$file"
  done
  for key in "${order[@]}"; do
    # The environment wins; so does an earlier source of this file in the same shell.
    if [ -n "${!key+x}" ]; then value="${!key}"; else value="${values[$key]}"; fi
    case "$value" in
      /*|[A-Za-z]:[/\\]*) ;;
      *) value="$MM_REPO/$value" ;;
    esac
    export "$key=$value"
  done
}
_mm_load_env
unset -f _mm_load_env
