"""Offline stand-in for a Google Sheet (the small part of gspread the bot uses). Each tab is a CSV file in a folder.
Used by `python src/leadbot.py --local` (try the bot with no Google account) and by the tests (mocked network)."""
import csv
import os
import re

import gspread
from gspread.utils import a1_to_rowcol


class _FakeResp:
    def __init__(self, code, text):
        self.status_code, self.text = code, text

    def json(self):
        return {"error": {"code": self.status_code, "message": self.text, "status": "INVALID_ARGUMENT"}}


class LocalWorksheet:
    _ids = 0

    def __init__(self, title, rows=None):
        LocalWorksheet._ids += 1
        self.id, self.title, self.rows = LocalWorksheet._ids, title, [list(r) for r in (rows or [])]
        self.grid, self.hidden = 1000, False

    # -- size
    @property
    def row_count(self):
        return max(self.grid, len(self.rows))

    def add_rows(self, n):
        self.grid = self.row_count + int(n)

    @property
    def col_count(self):
        return max([26] + [len(r) for r in self.rows])

    def resize(self, rows=None, cols=None):
        pass

    # -- read
    def get_all_values(self):
        w = max([0] + [len(r) for r in self.rows])
        return [list(r) + [""] * (w - len(r)) for r in self.rows]

    def row_values(self, n):
        r = self.rows[n - 1] if 0 < n <= len(self.rows) else []
        out = [str(x) for x in r]
        while out and out[-1] == "":
            out.pop()
        return out

    def col_values(self, n):
        out = [str(r[n - 1]) if len(r) >= n else "" for r in self.rows]
        while out and out[-1] == "":
            out.pop()
        return out

    # -- write
    def _set(self, r, c, v):
        while len(self.rows) < r:
            self.rows.append([])
        row = self.rows[r - 1]
        while len(row) < c:
            row.append("")
        row[c - 1] = "" if v is None else str(v)              # like gspread: every cell reads back as text

    def update(self, values=None, range_name=None, value_input_option=None, **kw):
        if isinstance(values, str) and range_name is not None and not isinstance(range_name, str):
            values, range_name = range_name, values                 # tolerate the old (range, values) order
        r0, c0 = a1_to_rowcol(re.split(r":", range_name or "A1")[0])
        for i, row in enumerate(values or []):
            for j, v in enumerate(row):
                self._set(r0 + i, c0 + j, v)

    def append_rows(self, rows, value_input_option=None, **kw):
        last = len(self.rows)
        while last and not any(str(x) for x in self.rows[last - 1]):
            last -= 1
        self.rows = self.rows[:last] + [["" if x is None else str(x) for x in r] for r in rows]

    def append_row(self, row, **kw):
        self.append_rows([row])

    def clear(self):
        self.rows = []

    def batch_clear(self, ranges):
        for rg in ranges:
            a, _, b = rg.partition(":")
            r0, c0 = a1_to_rowcol(a)
            c1 = a1_to_rowcol(re.sub(r"[A-Z]+$", lambda m: m.group(0) + "1", b) if re.fullmatch(r"[A-Z]+", b) else b)[1] if b else c0
            for r in range(r0, len(self.rows) + 1):
                for c in range(c0, c1 + 1):
                    if len(self.rows[r - 1]) >= c:
                        self.rows[r - 1][c - 1] = ""

    def sort(self, *specs, range=None):
        body = self.rows[1:]
        for col, order in reversed(specs):                     # stable multi-key sort, like Google Sheets

            def key(r, col=col):
                v = r[col - 1] if len(r) >= col else ""
                try:
                    return (0, float(v))
                except (TypeError, ValueError):
                    return (1, str(v))
            body.sort(key=key, reverse=(order == "des"))
        self.rows = self.rows[:1] + body

    def update_title(self, t):
        self.title = t

    def freeze(self, **kw):
        pass

    def format(self, *a, **kw):
        pass

    def set_basic_filter(self, *a, **kw):
        pass


class LocalSheet:
    def __init__(self, folder=None):
        self.folder, self.tabs = folder, []
        if folder and os.path.isdir(folder):
            for f in sorted(os.listdir(folder)):
                if f.endswith(".csv"):
                    with open(os.path.join(folder, f), newline="", encoding="utf-8") as fh:
                        self.tabs.append(LocalWorksheet(f[:-4], list(csv.reader(fh))))

    def worksheet(self, title):
        for ws in self.tabs:
            if ws.title == title:
                return ws
        raise gspread.WorksheetNotFound(title)

    def add_worksheet(self, title, rows=1000, cols=26, **kw):
        ws = LocalWorksheet(title)
        self.tabs.append(ws)
        return ws

    def worksheets(self):
        return list(self.tabs)

    def values_batch_get(self, ranges, params=None):
        """Like the Sheets API values:batchGet for "'Tab'", "'Tab'!1:1" and "'Tab'!C:C" ranges."""
        major = (params or {}).get("majorDimension", "ROWS")
        out = []
        for rg in ranges:
            m = re.fullmatch(r"'((?:[^']|'')*)'(?:!(.*))?", rg) or re.fullmatch(r"([^!]*)(?:!(.*))?", rg)
            title, part = m.group(1).replace("''", "'"), m.group(2) or ""
            try:
                ws = self.worksheet(title)
            except gspread.WorksheetNotFound:
                raise gspread.exceptions.APIError(_FakeResp(400, f"Unable to parse range: {rg}"))
            grid = ws.get_all_values()
            if re.fullmatch(r"\d+:\d+", part):
                a, b = (int(x) for x in part.split(":"))
                grid = grid[a - 1:b]
            elif re.fullmatch(r"[A-Z]+:[A-Z]+", part):
                c0, c1 = (a1_to_rowcol(x + "1")[1] for x in part.split(":"))
                grid = [r[c0 - 1:c1] for r in grid]
            grid = [list(r) for r in grid]
            for r in grid:
                while r and r[-1] == "":
                    r.pop()
            while grid and not grid[-1]:
                grid.pop()
            if major == "COLUMNS":
                w = max([0] + [len(r) for r in grid])
                cols = [[(r[j] if j < len(r) else "") for r in grid] for j in range(w)]
                for c in cols:
                    while c and c[-1] == "":
                        c.pop()
                grid = cols
            out.append({"range": rg, "values": grid} if grid else {"range": rg})
        return {"valueRanges": out}

    def batch_update(self, body):
        for req in (body or {}).get("requests", []):
            props = (req.get("updateSheetProperties") or {}).get("properties") or {}
            if "hidden" in props:
                ws = next((w for w in self.tabs if w.id == props.get("sheetId")), None)
                if ws:
                    ws.hidden = bool(props["hidden"])
            if "index" in props:                               # tab moves (Dashboard first, niche order)
                ws = next((w for w in self.tabs if w.id == props.get("sheetId")), None)
                if ws:
                    self.tabs.remove(ws)
                    self.tabs.insert(props["index"], ws)
        return {}

    def save(self):
        if not self.folder:
            return
        os.makedirs(self.folder, exist_ok=True)
        for ws in self.tabs:
            name = re.sub(r'[<>:"/\\|?*]', "_", ws.title)
            with open(os.path.join(self.folder, name + ".csv"), "w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerows(ws.get_all_values())
