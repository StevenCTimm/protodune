#!/bin/bash
# Fix Rucio replicas on the tape-backed RSE: give them a (placeholder) path and
# mark them available with an epoch tombstone so the reaper removes the records.
#
# Usage:
#   replica_fix.sh [--apply] <scope>:<name>             one file
#   replica_fix.sh [--apply] --list <file|->            one <scope>:<name> per line
#   replica_fix.sh [--apply] --dataset <scope>:<name>   every file in a dataset
#
# Without --apply, only reports what would change; nothing is modified.
# With --apply, all updates run in a single transaction.
#
# Credentials: put the password in ~/.pgpass (chmod 600), e.g.
#   ifdb11.fnal.gov:5455:dune_rucio_prod:rucio:<password>
# or export PGPASSWORD before running.

set -euo pipefail

usage() {
    sed -n '5,8p' "$0" | sed 's/^# \{0,1\}//' >&2
    exit 1
}

apply=0
mode=did
arg=

while (( $# )); do
    case "$1" in
        --apply)   apply=1 ;;
        --list)    mode=list;    arg=${2:-}; shift ;;
        --dataset) mode=dataset; arg=${2:-}; shift ;;
        -h|--help) usage ;;
        -*)        echo "Unknown option: $1" >&2; usage ;;
        *)         [[ -z "$arg" ]] || usage; arg=$1 ;;
    esac
    shift
done

[[ -n "$arg" ]] || usage
if [[ "$mode" != list && "$arg" != *:* ]]; then
    echo "Expected <scope>:<name>, got: $arg" >&2
    exit 1
fi
if [[ "$mode" == list && "$arg" != - && ! -r "$arg" ]]; then
    echo "Cannot read list file: $arg" >&2
    exit 1
fi

RSE_ID='a9780baa-e23e-4835-9e2a-84d3b19261ae'

# --- SQL: build the temp table of target DIDs --------------------------------

load_sql() {
    cat <<'SQL'
CREATE TEMP TABLE raw_dids (did text);
CREATE TEMP TABLE targets (scope varchar(25), name varchar(255), PRIMARY KEY (scope, name));
SQL
    if [[ "$mode" == dataset ]]; then
        cat <<'SQL'
INSERT INTO targets (scope, name)
SELECT child_scope, child_name
FROM contents
WHERE scope = :'ds_scope' AND name = :'ds_name' AND child_type = 'F';
SQL
    else
        # DIDs arrive on psql's stdin; blank lines and #comments are ignored.
        cat <<'SQL'
\copy raw_dids FROM pstdin
DELETE FROM raw_dids WHERE btrim(did) = '' OR btrim(did) LIKE '#%';
DO $$
DECLARE bad text;
BEGIN
    SELECT string_agg(did, ', ') INTO bad
    FROM (SELECT did FROM raw_dids WHERE position(':' IN btrim(did)) = 0 LIMIT 5) b;
    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'Lines not in <scope>:<name> form: %', bad;
    END IF;
END $$;
INSERT INTO targets (scope, name)
SELECT DISTINCT split_part(d, ':', 1), substr(d, position(':' IN d) + 1)
FROM (SELECT btrim(did) AS d FROM raw_dids) t;
SQL
    fi
    cat <<'SQL'
ANALYZE targets;
SQL
}

summary_sql() {
    cat <<'SQL'
SELECT
    (SELECT count(*) FROM targets)                                         AS dids,
    count(r.name)                                                          AS replicas_on_rse,
    count(*) FILTER (WHERE r.path IS NULL AND r.tombstone IS NOT NULL)     AS need_path,
    count(*) FILTER (WHERE r.lock_cnt = 0
                       AND r.tombstone = '1970-01-01 00:00:00')            AS mark_available,
    count(*) FILTER (WHERE r.lock_cnt > 0)                                 AS locked
FROM targets t
JOIN replicas r ON r.scope = t.scope AND r.name = t.name AND r.rse_id = :'rse_id';

SELECT r.scope, r.name, r.state, r.path, r.tombstone, r.lock_cnt, r.updated_at
FROM targets t
JOIN replicas r ON r.scope = t.scope AND r.name = t.name AND r.rse_id = :'rse_id'
ORDER BY r.name
LIMIT 20;
SQL
}

update_sql() {
    cat <<'SQL'
UPDATE replicas r
SET path = '/pnfs/dune/tape_backed/dunepro'
FROM targets t
WHERE r.scope = t.scope
  AND r.name = t.name
  AND r.rse_id = :'rse_id'
  AND r.tombstone IS NOT NULL
  AND r.path IS NULL;

UPDATE replicas r
SET state = 'A',
    tombstone = '1970-01-01 00:00:00',
    updated_at = '1970-01-01 00:00:00'
FROM targets t
WHERE r.scope = t.scope
  AND r.name = t.name
  AND r.rse_id = :'rse_id'
  AND r.lock_cnt = 0
  AND r.tombstone = '1970-01-01 00:00:00';
SQL
}

build_sql() {
    echo 'BEGIN;'
    load_sql
    if (( apply )); then
        echo '\set QUIET off'   # show UPDATE row counts
        update_sql
        echo '\set QUIET on'
        echo '\echo After update (first 20 rows):'
        summary_sql
        echo 'COMMIT;'
        echo '\echo Committed.'
    else
        echo '\echo Dry run: counts of what would change (first 20 rows shown):'
        summary_sql
        echo 'ROLLBACK;'
        echo '\echo Dry run only. Re-run with --apply to make changes.'
    fi
}

# --- Run ---------------------------------------------------------------------

psql_args=(-h ifdb11.fnal.gov -p 5455 -U rucio -d dune_rucio_prod
           -X -q -v ON_ERROR_STOP=1 -v rse_id="$RSE_ID")

case "$mode" in
    did)
        echo "$arg" | psql "${psql_args[@]}" -f <(build_sql) ;;
    list)
        if [[ "$arg" == - ]]; then
            psql "${psql_args[@]}" -f <(build_sql)
        else
            psql "${psql_args[@]}" -f <(build_sql) < "$arg"
        fi ;;
    dataset)
        psql "${psql_args[@]}" -v ds_scope="${arg%%:*}" -v ds_name="${arg#*:}" \
            -f <(build_sql) < /dev/null ;;
esac
