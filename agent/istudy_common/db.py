"""PostgreSQL 連線：以 ctypes 直接呼叫 libpq（只用 Python 標準函式庫）。

agent 映像安裝 libpq5；主機上的 istudyctl 不直接連資料庫（透過 docker compose exec）。
SQL 參數一律用 %s 佔位，內部轉成 $1、$2…，由 libpq 以參數方式送出（不做字串拼接）。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import datetime as _dt
import decimal
import json
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

_lib = None
_lock = threading.Lock()


def _libpq():
    global _lib
    with _lock:
        if _lib is None:
            name = ctypes.util.find_library("pq") or "libpq.so.5"
            lib = ctypes.CDLL(name)
            lib.PQconnectdb.argtypes = [ctypes.c_char_p]
            lib.PQconnectdb.restype = ctypes.c_void_p
            lib.PQstatus.argtypes = [ctypes.c_void_p]
            lib.PQstatus.restype = ctypes.c_int
            lib.PQerrorMessage.argtypes = [ctypes.c_void_p]
            lib.PQerrorMessage.restype = ctypes.c_char_p
            lib.PQfinish.argtypes = [ctypes.c_void_p]
            lib.PQfinish.restype = None
            lib.PQexecParams.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p,
                                         ctypes.POINTER(ctypes.c_char_p), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
            lib.PQexecParams.restype = ctypes.c_void_p
            lib.PQexec.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            lib.PQexec.restype = ctypes.c_void_p
            lib.PQresultStatus.argtypes = [ctypes.c_void_p]
            lib.PQresultStatus.restype = ctypes.c_int
            lib.PQresultErrorMessage.argtypes = [ctypes.c_void_p]
            lib.PQresultErrorMessage.restype = ctypes.c_char_p
            lib.PQresultErrorField.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.PQresultErrorField.restype = ctypes.c_char_p
            lib.PQntuples.argtypes = [ctypes.c_void_p]
            lib.PQntuples.restype = ctypes.c_int
            lib.PQnfields.argtypes = [ctypes.c_void_p]
            lib.PQnfields.restype = ctypes.c_int
            lib.PQfname.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.PQfname.restype = ctypes.c_char_p
            lib.PQftype.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.PQftype.restype = ctypes.c_uint
            lib.PQgetvalue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            lib.PQgetvalue.restype = ctypes.c_void_p
            lib.PQgetlength.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            lib.PQgetlength.restype = ctypes.c_int
            lib.PQgetisnull.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
            lib.PQgetisnull.restype = ctypes.c_int
            lib.PQcmdTuples.argtypes = [ctypes.c_void_p]
            lib.PQcmdTuples.restype = ctypes.c_char_p
            lib.PQclear.argtypes = [ctypes.c_void_p]
            lib.PQclear.restype = None
            lib.PQtransactionStatus.argtypes = [ctypes.c_void_p]
            lib.PQtransactionStatus.restype = ctypes.c_int
            _lib = lib
    return _lib


class DBError(Exception):
    def __init__(self, message: str, sqlstate: str = ""):
        super().__init__(message)
        self.sqlstate = sqlstate


PGRES_COMMAND_OK = 1
PGRES_TUPLES_OK = 2
CONNECTION_OK = 0
PG_DIAG_SQLSTATE = ord("C")


def _parse_array(s: str, conv) -> list:
    """解析一維 PostgreSQL 陣列字面值，例如 {a,"b c",NULL}。"""
    if s == "{}":
        return []
    body = s[1:-1]
    out, cur, quoted, esc, was_quoted = [], [], False, False, False
    for ch in body:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == '"':
            quoted = not quoted
            was_quoted = True
        elif ch == "," and not quoted:
            val = "".join(cur)
            out.append(None if (val == "NULL" and not was_quoted) else conv(val))
            cur, was_quoted = [], False
        else:
            cur.append(ch)
    val = "".join(cur)
    out.append(None if (val == "NULL" and not was_quoted) else conv(val))
    return out


def _ts(v: str) -> _dt.datetime:
    v = v.replace(" ", "T", 1)
    if v.endswith(("+00", "-00")) or (len(v) > 3 and v[-3] in "+-" and v[-2:].isdigit()):
        v = v + ":00"
    return _dt.datetime.fromisoformat(v)


_CONVERTERS = {
    16: lambda v: v == "t",
    20: int, 21: int, 23: int, 26: int,
    700: float, 701: float, 1700: decimal.Decimal,
    114: json.loads, 3802: json.loads,
    1082: _dt.date.fromisoformat,
    1114: _ts, 1184: _ts,
    1009: lambda v: _parse_array(v, str), 1015: lambda v: _parse_array(v, str),
    1016: lambda v: _parse_array(v, int), 1007: lambda v: _parse_array(v, int),
    1000: lambda v: _parse_array(v, lambda x: x == "t"),
}


def _array_literal(items: Sequence[Any]) -> str:
    parts = []
    for it in items:
        if it is None:
            parts.append("NULL")
        else:
            s = str(it).replace("\\", "\\\\").replace('"', '\\"')
            parts.append('"' + s + '"')
    return "{" + ",".join(parts) + "}"


def _param(v: Any) -> Optional[bytes]:
    if v is None:
        return None
    if isinstance(v, bool):
        return b"true" if v else b"false"
    if isinstance(v, (dict,)):
        return json.dumps(v, ensure_ascii=False).encode()
    if isinstance(v, (list, tuple)):
        return _array_literal(v).encode()
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat().encode()
    if isinstance(v, bytes):
        return v
    return str(v).encode()


def _convert_sql(sql: str) -> str:
    """把 %s 轉成 $1…；'%%' 轉成 '%'。忽略字串常值中的 %s。"""
    out, n, i, in_str = [], 0, 0, False
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == "%" and not in_str and sql[i:i + 2] == "%s":
            n += 1
            out.append(f"${n}")
            i += 1
        elif ch == "%" and sql[i:i + 2] == "%%":
            out.append("%")
            i += 1
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class Connection:
    def __init__(self, conninfo: str):
        self._lib = _libpq()
        self._conn = self._lib.PQconnectdb(conninfo.encode())
        if self._lib.PQstatus(self._conn) != CONNECTION_OK:
            msg = (self._lib.PQerrorMessage(self._conn) or b"").decode("utf-8", "replace").strip()
            self._lib.PQfinish(self._conn)
            self._conn = None
            raise DBError("無法連線資料庫：" + msg)
        self._depth = 0
        self.exec_raw("SET TIME ZONE 'Asia/Taipei'")

    def close(self) -> None:
        if self._conn:
            self._lib.PQfinish(self._conn)
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def ok(self) -> bool:
        return bool(self._conn) and self._lib.PQstatus(self._conn) == CONNECTION_OK

    def _check(self, res) -> None:
        st = self._lib.PQresultStatus(res)
        if st not in (PGRES_COMMAND_OK, PGRES_TUPLES_OK):
            msg = (self._lib.PQresultErrorMessage(res) or b"").decode("utf-8", "replace").strip()
            state = (self._lib.PQresultErrorField(res, PG_DIAG_SQLSTATE) or b"").decode()
            self._lib.PQclear(res)
            raise DBError(msg or "資料庫錯誤", state)

    def exec_raw(self, sql: str) -> None:
        """執行不帶參數的 SQL（可多個敘述，例如 schema.sql）。"""
        if not self._conn:
            raise DBError("連線已關閉")
        res = self._lib.PQexec(self._conn, sql.encode())
        if not res:
            raise DBError("資料庫沒有回應")
        self._check(res)
        self._lib.PQclear(res)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        if not self._conn:
            raise DBError("連線已關閉")
        values = [_param(p) for p in params]
        arr = (ctypes.c_char_p * max(1, len(values)))(*values)
        res = self._lib.PQexecParams(self._conn, _convert_sql(sql).encode(), len(values), None, arr, None, None, 0)
        if not res:
            raise DBError((self._lib.PQerrorMessage(self._conn) or b"").decode("utf-8", "replace") or "資料庫沒有回應")
        self._check(res)
        try:
            rows = []
            ntup, nf = self._lib.PQntuples(res), self._lib.PQnfields(res)
            if nf:
                names = [self._lib.PQfname(res, j).decode() for j in range(nf)]
                types = [self._lib.PQftype(res, j) for j in range(nf)]
                for i in range(ntup):
                    row = {}
                    for j in range(nf):
                        if self._lib.PQgetisnull(res, i, j):
                            row[names[j]] = None
                            continue
                        ptr = self._lib.PQgetvalue(res, i, j)
                        ln = self._lib.PQgetlength(res, i, j)
                        raw = ctypes.string_at(ptr, ln).decode("utf-8", "replace")
                        conv = _CONVERTERS.get(types[j])
                        row[names[j]] = conv(raw) if conv else raw
                    rows.append(row)
            self.rowcount = int((self._lib.PQcmdTuples(res) or b"0").decode() or 0)
            return rows
        finally:
            self._lib.PQclear(res)

    def one(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        rows = self.execute(sql, params)
        return rows[0] if rows else None

    def val(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.one(sql, params)
        return next(iter(row.values())) if row else None

    @contextmanager
    def transaction(self) -> Iterator["Connection"]:
        """巢狀呼叫時只有最外層真正 BEGIN／COMMIT。"""
        if self._depth == 0:
            self.exec_raw("BEGIN")
        self._depth += 1
        try:
            yield self
            self._depth -= 1
            if self._depth == 0:
                self.exec_raw("COMMIT")
        except BaseException:
            self._depth -= 1
            if self._depth == 0:
                try:
                    self.exec_raw("ROLLBACK")
                except DBError:
                    pass
            raise


def conninfo_from_env(env: Optional[dict] = None) -> str:
    import os
    e = env or os.environ

    def q(v: str) -> str:
        return "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"

    parts = {
        "host": e.get("DB_HOST", "127.0.0.1"),
        "port": e.get("DB_PORT", "5432"),
        "dbname": e.get("DB_NAME", "istudy"),
        "user": e.get("DB_USER", "istudy"),
        "password": e.get("DB_PASSWORD", ""),
        "connect_timeout": "5",
        "application_name": "istudy-agent",
    }
    return " ".join(f"{k}={q(v)}" for k, v in parts.items() if v != "")


def connect(conninfo: Optional[str] = None) -> Connection:
    return Connection(conninfo or conninfo_from_env())
