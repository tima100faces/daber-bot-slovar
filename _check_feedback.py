import sys
import traceback

try:
    import psycopg2
    conn = psycopg2.connect(
        host="127.0.0.1",
        port=5434,
        dbname="daber_dict",
        user="postgres"
    )
    cur = conn.cursor()
    
    # Check tables exist
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
    tables = [r[0] for r in cur.fetchall()]
    print(f"Таблицы в БД: {tables}")
    
    if 'user_feedback' not in tables:
        print("⚠ Таблица user_feedback не найдена!")
        cur.close()
        conn.close()
        sys.exit(0)
    
    # Count all
    cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE resolved=false) FROM user_feedback")
    total, unresolved = cur.fetchone()
    print(f"Всего записей: {total}, неразрешённых: {unresolved}")
    
    if unresolved == 0:
        print("Нет неразрешённых сообщений.")
        cur.close()
        conn.close()
        sys.exit(0)
    
    cur.execute("""
        SELECT id, word_id, comment, created_at, resolved
        FROM user_feedback
        WHERE resolved = false
        ORDER BY created_at DESC
    """)
    rows = cur.fetchall()
    
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    
    for row in rows:
        fb_id, word_id, comment, created_at, resolved = row
        age = now - created_at.replace(tzinfo=timezone.utc)
        
        cur.execute("""
            SELECT headword, translit, translation, pos, lexical_category
            FROM words
            WHERE id = %s
        """, (word_id,))
        word = cur.fetchone()
        
        print(f"\n--- Feedback #{fb_id} ---")
        print(f"Word ID: {word_id}")
        if word:
            print(f"  Слово:       {word[0]}")
            print(f"  Транслит:    {word[1]}")
            print(f"  Перевод:     {word[2]}")
            print(f"  Часть речи:  {word[3]}")
            print(f"  Категория:   {word[4]}")
        else:
            print(f"  ⚠ Слово с ID {word_id} не найдено!")
        print(f"  Комментарий пользователя: {comment}")
        print(f"  Создано: {created_at.strftime('%Y-%m-%d %H:%M')} ({age.days}д {age.seconds//3600}ч назад)")
    
    cur.close()
    conn.close()

except Exception as e:
    print(f"ERROR: {e}")
    traceback.print_exc()
    sys.exit(1)
