#!/bin/bash

# Variables
BACKUP_DIR="/home/user/daily_backup"
DATE=$(date +%Y-%m-%d_%H-%M-%S)
BACKUP_NAME="backup_$DATE"
DB_USER="backup_user_name"
DB_PASSWORD="password"




DB_HOST="localhost"
SKIP_DBS="dummy2|dummy3"

# Create backup directory if it does not exist
mkdir -p $BACKUP_DIR/$BACKUP_NAME

# Set up environment variables for PostgreSQL
export PGPASSWORD=$DB_PASSWORD

# Function to log and exit on error
function log_and_exit_on_error {
    if [ $? -ne 0 ]; then
        echo "Error: $1"
        exit 1
    fi
}

# List databases except those in SKIP_DBS
databases=$(psql -U $DB_USER -h $DB_HOST -d postgres -t -c "SELECT datname FROM pg_database WHERE datistemplate = false;" | grep -vE "($SKIP_DBS|template0|template1|postgres)")
log_and_exit_on_error "Failed to list databases."

# Dump global objects
echo "Dumping global objects..."
pg_dumpall -U $DB_USER -h $DB_HOST --globals-only --verbose > $BACKUP_DIR/$BACKUP_NAME/global_objects.sql
log_and_exit_on_error "Failed to dump global objects."

# Dump all databases except those in SKIP_DBS
for db in $databases; do
    db=$(echo $db | xargs)  # Trim whitespace
    echo "Backing up database: $db"
    pg_dump -U $DB_USER -h $DB_HOST -F c -b --verbose $db -f "$BACKUP_DIR/$BACKUP_NAME/$db.backup"
    log_and_exit_on_error "Failed to backup database $db."
done

# Compress the backup directory
echo "Compressing backup directory..."
tar -czf $BACKUP_DIR/$BACKUP_NAME.tar.gz -C $BACKUP_DIR $BACKUP_NAME
log_and_exit_on_error "Failed to compress backup directory."

# Delete the uncompressed backup directory
echo "Deleting uncompressed backup directory..."
rm -rf $BACKUP_DIR/$BACKUP_NAME
log_and_exit_on_error "Failed to delete uncompressed backup directory."

# Delete old compressed backups, keeping only the latest 7
cd $BACKUP_DIR
ls -t *.tar.gz | awk 'NR>7' | xargs rm -f
log_and_exit_on_error "Failed to delete old backups."

echo "Backup created: $BACKUP_DIR/$BACKUP_NAME.tar.gz"

