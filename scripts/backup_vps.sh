#!/usr/bin/env bash
# Nightly off-site backup for the SCL production VPS.
#
# Backs up everything under data/ (SQLite DB via crash-safe `sqlite3 .backup`,
# team uploads, archived match CSVs, scorer_config.json) plus .env, then pushes
# them to a SEPARATE private GitHub repo (scl-backups).
#
# Install on the VPS (as root):
#   1. Create a private repo on GitHub:  https://github.com/new  ->  "scl-backups"
#   2. ssh-keygen -t ed25519 -f /root/.ssh/scl_backups -N ""
#   3. GitHub repo -> Settings -> Deploy keys -> Add deploy key:
#      paste /root/.ssh/scl_backups.pub and TICK "Allow write access"
#   4. Clone it with that key:
#        GIT_SSH_COMMAND='ssh -i /root/.ssh/scl_backups' \
#          git clone git@github.com:<YOUR_USER>/scl-backups.git /root/scl-backups
#   5. Add the cron entry (runs at 02:00 server time = 07:00 PKT if the VPS
#      is on UTC; check with `date`):
#        crontab -e
#        0 2 * * * /root/scl-official/scripts/backup_vps.sh >> /var/log/scl-backup.log 2>&1
#
# Restore: stop the server, replace data/scl.db with the snapshot's scl.db and
# extract data-rest.tar.gz back over data/, restart (commands in the chat guide).
set -euo pipefail

APP_DIR="${APP_DIR:-/root/scl-official}"
BACKUP_DIR="${BACKUP_DIR:-/root/scl-backups}"
SSH_KEY="${SSH_KEY:-/root/.ssh/scl_backups}"
STAMP="$(date +%Y-%m-%d_%H%M)"
RETENTION="${RETENTION:-30}"   # how many daily snapshots to keep

SNAP="$BACKUP_DIR/snapshots/$STAMP"
mkdir -p "$SNAP"

# 1. Crash-safe SQLite snapshot. Unlike `cp`, this produces a consistent
#    snapshot even if a write is happening right now.
sqlite3 "$APP_DIR/data/scl.db" ".backup '$SNAP/scl.db'"

# 2. Everything else under data/ in one tar: team-uploaded logos/banners,
#    archived match CSVs (data/matches/), scorer_config.json, and any future
#    runtime files. The live scl.db is excluded (it was snapshotted above) so
#    the tar never catches a half-written copy. brandings/scl is already in the
#    app repo, but it is tiny so include it — keeps a backup self-contained.
#    NOTE: this must run from a directory above data/ and the whole dir must be
#    readable while the server is live — safe, since only the .db is ever open
#    for writing and that one is handled by .backup above.
tar -czf "$SNAP/data-rest.tar.gz" -C "$APP_DIR/data" --exclude='scl.db' .

# 3. Secrets (.env holds SCL_SECRET_KEY etc.). Skip this if you'd rather not
#    store secrets in the backup repo — the DB is the critical part.
if [ -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env" "$SNAP/env.backup"
fi

# 4. Commit + push with the deploy key. Uses a throwaway author identity so the
#    commit never depends on the VPS's git config.
export GIT_SSH_COMMAND="ssh -i $SSH_KEY -o IdentitiesOnly=yes"
cd "$BACKUP_DIR"
git add -A
git -c user.name="scl-backup" -c user.email="backup@scl.local" \
  commit -m "backup $STAMP" >/dev/null
git push -q origin main

# 5. Prune old snapshots on disk (note: git history still keeps every push,
#    which is the point of an off-site backup).
ls -1dt "$BACKUP_DIR"/snapshots/* 2>/dev/null | tail -n +"$((RETENTION + 1))" | xargs -r rm -rf

echo "Backup $STAMP pushed to $BACKUP_DIR"
