import aiosqlite, json, logging, os

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = "data/bot.db"):
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self):
        dirpath = os.path.dirname(self.db_path)
        if dirpath:
            os.makedirs(dirpath, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS kv_store (
                namespace TEXT NOT NULL,
                key       TEXT NOT NULL,
                value     TEXT,
                PRIMARY KEY (namespace, key)
            )
        """)
        await self._conn.commit()

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def execute(self, sql, params=()):
        cur = await self._conn.execute(sql, params)
        await self._conn.commit()
        return cur

    async def fetchall(self, sql, params=()):
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchall()

    async def fetchone(self, sql, params=()):
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def kv_set(self, ns, key, value):
        await self.execute(
            "INSERT OR REPLACE INTO kv_store VALUES(?,?,?)",
            (ns, key, json.dumps(value, ensure_ascii=False)),
        )

    async def kv_get(self, ns, key, default=None):
        row = await self.fetchone(
            "SELECT value FROM kv_store WHERE namespace=? AND key=?", (ns, key)
        )
        return json.loads(row["value"]) if row else default

    async def kv_delete(self, ns, key):
        await self.execute(
            "DELETE FROM kv_store WHERE namespace=? AND key=?", (ns, key)
        )
