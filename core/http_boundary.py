"""Local HTTP request checks shared by the two service adapters."""

MAX_BODY = 1_048_576


def local_request_error(headers, port: int, *, write: bool = False) -> str | None:
    host = headers.get("Host", "").lower()
    if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
        return "只接受本地工作台 Host"
    origin = headers.get("Origin")
    if origin is not None and origin != f"http://{host}":
        return "拒绝跨来源请求"
    if headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return "拒绝跨站点请求"
    if write and headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
        return "只接受 application/json"
    return None


def body_length(headers) -> int:
    raw = headers.get("Content-Length", "0")
    if headers.get("Transfer-Encoding") or len(raw) > 20 or not raw.isascii() or not raw.isdecimal():
        raise ValueError("请求体长度不合法")
    length = int(raw)
    if length > MAX_BODY:
        raise OverflowError("请求体超过 1 MiB")
    return length
