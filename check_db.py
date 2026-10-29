import sqlite3

conn = sqlite3.connect('D:/Code Playground/bakchodi/Backend/watch_party.db')
cursor = conn.cursor()

print('rooms schema:')
cursor.execute('PRAGMA table_info(rooms)')
for row in cursor.fetchall():
    print(f'  {row}')

print()
print('room_state schema:')
cursor.execute('PRAGMA table_info(room_state)')
for row in cursor.fetchall():
    print(f'  {row}')

# Also check if host_secret_hash exists in any table
print()
print('Checking for host_secret_hash in all tables:')
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
for table in tables:
    cursor.execute(f'PRAGMA table_info({table[0]})')
    cols = cursor.fetchall()
    for col in cols:
        if 'host_secret' in col[1].lower() or 'hash' in col[1].lower():
            print(f'  Found in {table[0]}: {col}')

conn.close()