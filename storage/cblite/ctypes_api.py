"""Thin ctypes wrapper for the libcblite C API.

Wraps only the subset needed for graph storage: database lifecycle,
collections, document CRUD, N1QL/SQL++ queries, value indexes, and
vector indexes.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import platform
import sys
from ctypes import (
    POINTER,
    Structure,
    byref,
    c_bool,
    c_char_p,
    c_double,
    c_int,
    c_int32,
    c_int64,
    c_size_t,
    c_uint,
    c_uint8,
    c_uint32,
    c_uint64,
    c_void_p,
    cdll,
)
from pathlib import Path

from .errors import CouchbaseLiteError, CouchbaseLiteNotAvailable, CouchbaseLiteNotFound


# ---------------------------------------------------------------------------
# Fleece / CBL struct definitions
# ---------------------------------------------------------------------------

class FLSlice(Structure):
    _fields_ = [
        ("buf", c_void_p),
        ("size", c_size_t),
    ]


FLString = FLSlice
FLSliceResult = FLSlice


class CBLError(Structure):
    _fields_ = [
        ("domain", c_uint8),
        ("code", c_int),
        ("internal_info", c_uint),
    ]


class CBLDatabaseConfiguration(Structure):
    _fields_ = [
        ("directory", FLSlice),
        ("fullSync", c_bool),
    ]


class CBLValueIndexConfiguration(Structure):
    _fields_ = [
        ("expressionLanguage", c_uint32),
        ("expressions", FLSlice),
        ("where", FLSlice),
    ]


class CBLVectorIndexConfiguration(Structure):
    _fields_ = [
        ("expressionLanguage", c_uint32),
        ("expression", FLSlice),
        ("dimensions", c_uint),
        ("centroids", c_uint),
        ("isLazy", c_bool),
        ("encoding", c_void_p),
        ("metric", c_uint32),
        ("minTrainingSize", c_uint),
        ("maxTrainingSize", c_uint),
        ("numProbes", c_uint),
    ]


# ---------------------------------------------------------------------------
# FLSlice helpers
# ---------------------------------------------------------------------------

_NULL_SLICE = FLSlice(None, 0)


def _to_flslice(s: str | None, keepalive: list) -> FLSlice:
    """Convert a Python string to an FLSlice.

    The encoded bytes object is appended to *keepalive* so it stays alive
    while the C layer holds the pointer.
    """
    if s is None:
        return _NULL_SLICE
    b = s.encode("utf-8")
    keepalive.append(b)
    return FLSlice(ctypes.cast(ctypes.c_char_p(b), c_void_p), len(b))


def _from_flslice(sl: FLSlice) -> str | None:
    """Read an FLSlice / FLString into a Python str (or None)."""
    if not sl.buf or sl.size == 0:
        return None
    return ctypes.string_at(sl.buf, sl.size).decode("utf-8")


def _from_flsliceresult(sl: FLSliceResult, lib) -> str | None:
    """Read an FLSliceResult into a Python str, then release it."""
    val = _from_flslice(sl)
    if sl.buf:
        lib._FLBuf_Release(sl.buf)
    return val


# ---------------------------------------------------------------------------
# Library loading
# ---------------------------------------------------------------------------

def _load_library() -> ctypes.CDLL:
    """Load libcblite, raising CouchbaseLiteNotAvailable on failure."""
    explicit = os.environ.get("CBLITE_LIB_PATH")
    if explicit:
        try:
            return cdll.LoadLibrary(explicit)
        except OSError as exc:
            raise CouchbaseLiteNotAvailable(
                f"Cannot load libcblite from CBLITE_LIB_PATH={explicit}: {exc}"
            ) from exc

    system = platform.system()
    candidates: list[str] = []
    if system == "Darwin":
        candidates = [
            "libcblite.dylib",
            "/opt/homebrew/lib/libcblite.dylib",
            "/usr/local/lib/libcblite.dylib",
        ]
    elif system == "Linux":
        candidates = ["libcblite.so.3", "libcblite.so"]
    else:
        candidates = ["libcblite.so"]

    for name in candidates:
        try:
            return cdll.LoadLibrary(name)
        except OSError:
            continue

    # Last resort: ask ctypes.util
    found = ctypes.util.find_library("cblite")
    if found:
        try:
            return cdll.LoadLibrary(found)
        except OSError:
            pass

    hint = ""
    if system == "Darwin":
        hint = "\n  brew install --cask libcblite-community"
    raise CouchbaseLiteNotAvailable(
        "libcblite shared library not found on this system."
        + hint
    )


def _bind(lib: ctypes.CDLL) -> ctypes.CDLL:
    """Declare argtypes / restypes for every function we use.

    Note: In CBL 4.x many per-type Release/Retain functions are inlined
    and delegate to CBL_Release / CBL_Retain.  FLSliceResult_Release is
    also inlined and delegates to _FLBuf_Release(s.buf).
    """

    # -- Generic ref-counting (replaces per-type Release/Retain) --
    lib.CBL_Release.argtypes = [c_void_p]
    lib.CBL_Release.restype = None

    lib.CBL_Retain.argtypes = [c_void_p]
    lib.CBL_Retain.restype = c_void_p

    # -- Fleece slice release (_FLBuf_Release(buf) replaces FLSliceResult_Release) --
    lib._FLBuf_Release.argtypes = [c_void_p]
    lib._FLBuf_Release.restype = None

    # -- CBLError --
    lib.CBLError_Message.argtypes = [POINTER(CBLError)]
    lib.CBLError_Message.restype = FLSliceResult

    # -- Database --
    lib.CBLDatabase_Open.argtypes = [FLSlice, POINTER(CBLDatabaseConfiguration), POINTER(CBLError)]
    lib.CBLDatabase_Open.restype = c_void_p

    lib.CBLDatabase_Close.argtypes = [c_void_p, POINTER(CBLError)]
    lib.CBLDatabase_Close.restype = c_bool

    lib.CBLDatabase_BeginTransaction.argtypes = [c_void_p, POINTER(CBLError)]
    lib.CBLDatabase_BeginTransaction.restype = c_bool

    lib.CBLDatabase_EndTransaction.argtypes = [c_void_p, c_bool, POINTER(CBLError)]
    lib.CBLDatabase_EndTransaction.restype = c_bool

    # -- Collection --
    lib.CBLDatabase_CreateCollection.argtypes = [c_void_p, FLSlice, FLSlice, POINTER(CBLError)]
    lib.CBLDatabase_CreateCollection.restype = c_void_p

    lib.CBLDatabase_Collection.argtypes = [c_void_p, FLSlice, FLSlice, POINTER(CBLError)]
    lib.CBLDatabase_Collection.restype = c_void_p

    lib.CBLCollection_Count.argtypes = [c_void_p]
    lib.CBLCollection_Count.restype = c_uint64

    lib.CBLCollection_PurgeDocumentByID.argtypes = [c_void_p, FLSlice, POINTER(CBLError)]
    lib.CBLCollection_PurgeDocumentByID.restype = c_bool

    # -- Document --
    lib.CBLDocument_CreateWithID.argtypes = [FLSlice]
    lib.CBLDocument_CreateWithID.restype = c_void_p

    lib.CBLDocument_SetJSON.argtypes = [c_void_p, FLSlice, POINTER(CBLError)]
    lib.CBLDocument_SetJSON.restype = c_bool

    lib.CBLDocument_CreateJSON.argtypes = [c_void_p]
    lib.CBLDocument_CreateJSON.restype = FLSliceResult

    lib.CBLCollection_SaveDocument.argtypes = [c_void_p, c_void_p, POINTER(CBLError)]
    lib.CBLCollection_SaveDocument.restype = c_bool

    lib.CBLCollection_GetDocument.argtypes = [c_void_p, FLSlice, POINTER(CBLError)]
    lib.CBLCollection_GetDocument.restype = c_void_p

    # -- Query --
    lib.CBLDatabase_CreateQuery.argtypes = [c_void_p, c_uint32, FLSlice, POINTER(c_int), POINTER(CBLError)]
    lib.CBLDatabase_CreateQuery.restype = c_void_p

    lib.CBLQuery_Execute.argtypes = [c_void_p, POINTER(CBLError)]
    lib.CBLQuery_Execute.restype = c_void_p

    lib.CBLResultSet_Next.argtypes = [c_void_p]
    lib.CBLResultSet_Next.restype = c_bool

    lib.CBLResultSet_ValueAtIndex.argtypes = [c_void_p, c_uint]
    lib.CBLResultSet_ValueAtIndex.restype = c_void_p

    lib.CBLQuery_ColumnCount.argtypes = [c_void_p]
    lib.CBLQuery_ColumnCount.restype = c_uint

    lib.CBLQuery_ColumnName.argtypes = [c_void_p, c_uint]
    lib.CBLQuery_ColumnName.restype = FLSlice

    lib.CBLQuery_SetParameters.argtypes = [c_void_p, c_void_p]
    lib.CBLQuery_SetParameters.restype = None

    # -- FLDoc (for query parameters) --
    lib.FLDoc_FromJSON.argtypes = [FLSlice, POINTER(CBLError)]
    lib.FLDoc_FromJSON.restype = c_void_p

    lib.FLDoc_GetRoot.argtypes = [c_void_p]
    lib.FLDoc_GetRoot.restype = c_void_p

    lib.FLDoc_Release.argtypes = [c_void_p]
    lib.FLDoc_Release.restype = None

    # -- FLValue reading --
    lib.FLValue_GetType.argtypes = [c_void_p]
    lib.FLValue_GetType.restype = c_int32

    lib.FLValue_AsBool.argtypes = [c_void_p]
    lib.FLValue_AsBool.restype = c_bool

    lib.FLValue_AsInt.argtypes = [c_void_p]
    lib.FLValue_AsInt.restype = c_int64

    lib.FLValue_AsDouble.argtypes = [c_void_p]
    lib.FLValue_AsDouble.restype = c_double

    lib.FLValue_AsString.argtypes = [c_void_p]
    lib.FLValue_AsString.restype = FLSlice

    lib.FLValue_ToJSON.argtypes = [c_void_p]
    lib.FLValue_ToJSON.restype = FLSliceResult

    # -- Index creation --
    lib.CBLCollection_CreateValueIndex.argtypes = [c_void_p, FLSlice, CBLValueIndexConfiguration, POINTER(CBLError)]
    lib.CBLCollection_CreateValueIndex.restype = c_bool

    # Vector index is Enterprise Edition only — bind only if symbol exists
    try:
        lib.CBLCollection_CreateVectorIndex.argtypes = [c_void_p, FLSlice, CBLVectorIndexConfiguration, POINTER(CBLError)]
        lib.CBLCollection_CreateVectorIndex.restype = c_bool
    except AttributeError:
        pass  # Community Edition — vector index not available

    return lib


# ---------------------------------------------------------------------------
# FLValue type constants
# ---------------------------------------------------------------------------

FL_UNDEFINED = -1
FL_NULL = 0
FL_BOOL = 1
FL_NUMBER = 2
FL_STRING = 3
FL_DATA = 4
FL_ARRAY = 5
FL_DICT = 6

# N1QL / SQL++ language constant
CBL_N1QL_LANGUAGE: int = 1

# Distance metric constants
CBL_DISTANCE_METRIC_EUCLIDEAN: int = 1
CBL_DISTANCE_METRIC_COSINE: int = 2


# ---------------------------------------------------------------------------
# High-level wrapper
# ---------------------------------------------------------------------------

class CBL:
    """High-level Couchbase Lite wrapper.

    All opaque CBL handles are ``ctypes.c_void_p``.  The wrapper keeps
    Python byte objects alive for the lifetime of any FLSlice that
    references them.
    """

    _cached_lib: ctypes.CDLL | None = None

    # -- Library management --------------------------------------------------

    @classmethod
    def _get_lib(cls) -> ctypes.CDLL:
        if cls._cached_lib is None:
            cls._cached_lib = _bind(_load_library())
        return cls._cached_lib

    @property
    def _lib(self) -> ctypes.CDLL:
        return self._get_lib()

    # -- Lifecycle -----------------------------------------------------------

    def __init__(self, db_path: str) -> None:
        """Open (or create) a database.

        *db_path* should look like ``'.graph_search/graph.cblite2'``.
        The ``.cblite2`` suffix (if present) is stripped to derive the
        database name; the parent directory is used as the storage
        directory.
        """
        p = Path(db_path)
        if p.suffix == ".cblite2":
            db_name = p.stem
            db_dir = str(p.parent.resolve())
        else:
            db_name = p.name
            db_dir = str(p.parent.resolve())

        self._keepalive: list[bytes] = []
        lib = self._lib

        name_sl = _to_flslice(db_name, self._keepalive)
        dir_sl = _to_flslice(db_dir, self._keepalive)

        cfg = CBLDatabaseConfiguration()
        cfg.directory = dir_sl
        cfg.fullSync = False

        err = CBLError()
        self._db = lib.CBLDatabase_Open(name_sl, byref(cfg), byref(err))
        self._check(err, "Failed to open database")
        if not self._db:
            raise CouchbaseLiteError("CBLDatabase_Open returned NULL")

    def close(self) -> None:
        """Close the database and release resources."""
        if self._db:
            err = CBLError()
            self._lib.CBLDatabase_Close(self._db, byref(err))
            self._lib.CBL_Release(self._db)
            self._db = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # -- Error checking ------------------------------------------------------

    def _check(self, err: CBLError, context: str = "") -> None:
        if err.code != 0:
            msg_sl = self._lib.CBLError_Message(byref(err))
            msg = _from_flsliceresult(msg_sl, self._lib) or "unknown error"
            full = f"{context}: {msg}" if context else msg
            if err.domain == 1 and err.code == 404:
                raise CouchbaseLiteNotFound(full, err.domain, err.code)
            raise CouchbaseLiteError(full, err.domain, err.code)

    # -- Collections ---------------------------------------------------------

    def get_or_create_collection(self, name: str, scope: str = "_default") -> c_void_p:
        """Create (or open) a collection, returning its opaque handle."""
        ka: list[bytes] = []
        name_sl = _to_flslice(name, ka)
        scope_sl = _to_flslice(scope, ka)
        err = CBLError()
        col = self._lib.CBLDatabase_CreateCollection(self._db, name_sl, scope_sl, byref(err))
        self._check(err, f"Failed to create/get collection '{scope}.{name}'")
        if not col:
            raise CouchbaseLiteError(f"Collection '{scope}.{name}' returned NULL")
        return col

    def collection_count(self, collection: c_void_p) -> int:
        """Return the number of documents in *collection*."""
        return int(self._lib.CBLCollection_Count(collection))

    # -- Document CRUD -------------------------------------------------------

    def save_document_json(self, collection: c_void_p, doc_id: str, json_str: str) -> None:
        """Create / update a document with the given JSON body."""
        ka: list[bytes] = []
        id_sl = _to_flslice(doc_id, ka)
        json_sl = _to_flslice(json_str, ka)

        doc = self._lib.CBLDocument_CreateWithID(id_sl)
        if not doc:
            raise CouchbaseLiteError("CBLDocument_CreateWithID returned NULL")
        try:
            err = CBLError()
            ok = self._lib.CBLDocument_SetJSON(doc, json_sl, byref(err))
            self._check(err, "CBLDocument_SetJSON")
            if not ok:
                raise CouchbaseLiteError("CBLDocument_SetJSON returned false")

            err2 = CBLError()
            ok2 = self._lib.CBLCollection_SaveDocument(collection, doc, byref(err2))
            self._check(err2, "CBLCollection_SaveDocument")
            if not ok2:
                raise CouchbaseLiteError("CBLCollection_SaveDocument returned false")
        finally:
            self._lib.CBL_Release(doc)

    def get_document_json(self, collection: c_void_p, doc_id: str) -> str | None:
        """Return the JSON body of a document, or ``None`` if not found."""
        ka: list[bytes] = []
        id_sl = _to_flslice(doc_id, ka)
        err = CBLError()
        doc = self._lib.CBLCollection_GetDocument(collection, id_sl, byref(err))
        if err.code != 0:
            # Not-found (domain=1, code=404) → return None
            if err.domain == 1 and err.code == 404:
                return None
            self._check(err, "CBLCollection_GetDocument")
        if not doc:
            return None
        try:
            sl = self._lib.CBLDocument_CreateJSON(doc)
            return _from_flsliceresult(sl, self._lib)
        finally:
            self._lib.CBL_Release(doc)

    def purge_document(self, collection: c_void_p, doc_id: str) -> None:
        """Purge a document by ID (no error if it doesn't exist)."""
        ka: list[bytes] = []
        id_sl = _to_flslice(doc_id, ka)
        err = CBLError()
        self._lib.CBLCollection_PurgeDocumentByID(collection, id_sl, byref(err))
        # Ignore not-found
        if err.code != 0 and not (err.domain == 1 and err.code == 404):
            self._check(err, "CBLCollection_PurgeDocumentByID")

    # -- Transactions --------------------------------------------------------

    def begin_transaction(self) -> None:
        err = CBLError()
        ok = self._lib.CBLDatabase_BeginTransaction(self._db, byref(err))
        self._check(err, "BeginTransaction")
        if not ok:
            raise CouchbaseLiteError("BeginTransaction returned false")

    def end_transaction(self, commit: bool = True) -> None:
        err = CBLError()
        ok = self._lib.CBLDatabase_EndTransaction(self._db, commit, byref(err))
        self._check(err, "EndTransaction")
        if not ok:
            raise CouchbaseLiteError("EndTransaction returned false")

    # -- Indexes -------------------------------------------------------------

    def create_value_index(
        self, collection: c_void_p, index_name: str, expressions: str
    ) -> None:
        """Create a value index on *collection*."""
        ka: list[bytes] = []
        name_sl = _to_flslice(index_name, ka)
        expr_sl = _to_flslice(expressions, ka)

        cfg = CBLValueIndexConfiguration()
        cfg.expressionLanguage = CBL_N1QL_LANGUAGE
        cfg.expressions = expr_sl
        cfg.where = _NULL_SLICE

        err = CBLError()
        ok = self._lib.CBLCollection_CreateValueIndex(collection, name_sl, cfg, byref(err))
        self._check(err, f"CreateValueIndex '{index_name}'")
        if not ok:
            raise CouchbaseLiteError(f"CreateValueIndex '{index_name}' returned false")

    @property
    def has_vector_index(self) -> bool:
        """Whether the loaded libcblite supports vector indexes (Enterprise Edition)."""
        try:
            _ = self._lib.CBLCollection_CreateVectorIndex
            return True
        except AttributeError:
            return False

    def create_vector_index(
        self,
        collection: c_void_p,
        index_name: str,
        expression: str,
        dimensions: int,
        centroids: int,
        metric: int = CBL_DISTANCE_METRIC_COSINE,
    ) -> bool:
        """Create a vector index on *collection*.

        Returns True if index was created, False if vector indexes are
        not available (Community Edition).
        """
        if not self.has_vector_index:
            return False

        ka: list[bytes] = []
        name_sl = _to_flslice(index_name, ka)
        expr_sl = _to_flslice(expression, ka)

        cfg = CBLVectorIndexConfiguration()
        cfg.expressionLanguage = CBL_N1QL_LANGUAGE
        cfg.expression = expr_sl
        cfg.dimensions = dimensions
        cfg.centroids = centroids
        cfg.isLazy = False
        cfg.encoding = None
        cfg.metric = metric
        cfg.minTrainingSize = 0
        cfg.maxTrainingSize = 0
        cfg.numProbes = 0

        err = CBLError()
        ok = self._lib.CBLCollection_CreateVectorIndex(collection, name_sl, cfg, byref(err))
        self._check(err, f"CreateVectorIndex '{index_name}'")
        if not ok:
            raise CouchbaseLiteError(f"CreateVectorIndex '{index_name}' returned false")
        return True

    # -- Queries -------------------------------------------------------------

    def _create_and_run_query(
        self, sql: str, params_json: str | None = None
    ) -> tuple[c_void_p, c_void_p]:
        """Compile and execute a query, returning (query, result_set).

        Caller is responsible for releasing both handles.
        """
        ka: list[bytes] = []
        sql_sl = _to_flslice(sql, ka)
        err_pos = c_int(-1)
        err = CBLError()

        query = self._lib.CBLDatabase_CreateQuery(
            self._db, CBL_N1QL_LANGUAGE, sql_sl, byref(err_pos), byref(err)
        )
        self._check(err, f"CreateQuery (error at pos {err_pos.value})")
        if not query:
            raise CouchbaseLiteError(
                f"CreateQuery returned NULL (error pos {err_pos.value})"
            )

        # Set parameters if provided
        if params_json is not None:
            self._set_query_params(query, params_json)

        err2 = CBLError()
        rs = self._lib.CBLQuery_Execute(query, byref(err2))
        self._check(err2, "Query_Execute")
        if not rs:
            self._lib.CBL_Release(query)
            raise CouchbaseLiteError("Query_Execute returned NULL")

        return query, rs

    def _set_query_params(self, query: c_void_p, params_json: str) -> None:
        """Set query parameters from a JSON string."""
        ka: list[bytes] = []
        json_sl = _to_flslice(params_json, ka)
        err = CBLError()
        doc = self._lib.FLDoc_FromJSON(json_sl, byref(err))
        self._check(err, "FLDoc_FromJSON for query params")
        if not doc:
            raise CouchbaseLiteError("FLDoc_FromJSON returned NULL")
        try:
            root = self._lib.FLDoc_GetRoot(doc)
            if root:
                self._lib.CBLQuery_SetParameters(query, root)
        finally:
            self._lib.FLDoc_Release(doc)

    def _flvalue_to_python(self, val: c_void_p):
        """Convert an FLValue to a Python object via JSON round-trip."""
        if not val:
            return None
        vtype = self._lib.FLValue_GetType(val)
        if vtype == FL_UNDEFINED or vtype == FL_NULL:
            return None
        if vtype == FL_BOOL:
            return self._lib.FLValue_AsBool(val)
        if vtype == FL_NUMBER:
            d = self._lib.FLValue_AsDouble(val)
            i = self._lib.FLValue_AsInt(val)
            return i if d == float(i) else d
        if vtype == FL_STRING:
            sl = self._lib.FLValue_AsString(val)
            return _from_flslice(sl)
        # For arrays, dicts, and data fall back to JSON round-trip
        sl = self._lib.FLValue_ToJSON(val)
        raw = _from_flsliceresult(sl, self._lib)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def execute_query(self, sql: str, params_json: str | None = None) -> list[dict]:
        """Execute a SQL++ query and return results as a list of dicts.

        Each dict maps column name → Python value.
        """
        query, rs = self._create_and_run_query(sql, params_json)
        try:
            ncols = self._lib.CBLQuery_ColumnCount(query)
            col_names: list[str] = []
            for i in range(ncols):
                sl = self._lib.CBLQuery_ColumnName(query, i)
                col_names.append(_from_flslice(sl) or f"col{i}")

            results: list[dict] = []
            while self._lib.CBLResultSet_Next(rs):
                row: dict = {}
                for i, name in enumerate(col_names):
                    val = self._lib.CBLResultSet_ValueAtIndex(rs, i)
                    row[name] = self._flvalue_to_python(val)
                results.append(row)
            return results
        finally:
            self._lib.CBL_Release(rs)
            self._lib.CBL_Release(query)

    def execute_query_raw(self, sql: str, params_json: str | None = None) -> list[list]:
        """Execute a SQL++ query and return results as a list of lists.

        Each inner list contains the column values in order.
        """
        query, rs = self._create_and_run_query(sql, params_json)
        try:
            ncols = self._lib.CBLQuery_ColumnCount(query)
            results: list[list] = []
            while self._lib.CBLResultSet_Next(rs):
                row: list = []
                for i in range(ncols):
                    val = self._lib.CBLResultSet_ValueAtIndex(rs, i)
                    row.append(self._flvalue_to_python(val))
                results.append(row)
            return results
        finally:
            self._lib.CBL_Release(rs)
            self._lib.CBL_Release(query)
