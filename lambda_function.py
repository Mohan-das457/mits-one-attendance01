"""
AWS Lambda Handler for Flask Attendance App (MITS ONE).
Adapts Lambda Function URL / API Gateway events to WSGI.
"""
import io
import os
import base64
import urllib.parse
from server import app


def lambda_handler(event, context):
    # Handle Payload Format 2.0 (Lambda Function URL & HTTP API v2)
    raw_path = event.get("rawPath", "/")
    raw_query = event.get("rawQueryString", "")
    http_ctx = event.get("requestContext", {}).get("http", {})
    method = http_ctx.get("method", event.get("httpMethod", "GET"))

    headers = event.get("headers") or {}

    # Body decoding
    body_data = event.get("body") or ""
    if event.get("isBase64Encoded", False):
        body_bytes = base64.b64decode(body_data)
    else:
        body_bytes = body_data.encode("utf-8")

    # Build standard WSGI environment
    environ = {
        "REQUEST_METHOD": method,
        "SCRIPT_NAME": "",
        "PATH_INFO": urllib.parse.unquote(raw_path),
        "QUERY_STRING": raw_query,
        "SERVER_NAME": headers.get("host", "lambda.local"),
        "SERVER_PORT": "443",
        "SERVER_PROTOCOL": http_ctx.get("protocol", "HTTP/1.1"),
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "https",
        "wsgi.input": io.BytesIO(body_bytes),
        "wsgi.errors": io.StringIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "CONTENT_LENGTH": str(len(body_bytes)),
    }

    if "content-type" in headers:
        environ["CONTENT_TYPE"] = headers["content-type"]

    for key, value in headers.items():
        wsgi_key = "HTTP_" + key.upper().replace("-", "_")
        environ[wsgi_key] = value

    response_status = [200]
    response_headers = []

    def start_response(status, res_headers, exc_info=None):
        response_status[0] = int(status.split(" ")[0])
        response_headers.extend(res_headers)
        return lambda data: None

    # Call Flask WSGI app
    response_iter = app(environ, start_response)
    response_body = b"".join(response_iter)

    # Format headers and detect encoding
    out_headers = {}
    is_base64 = False
    for k, v in response_headers:
        out_headers[k] = v

    content_type = out_headers.get("Content-Type", out_headers.get("content-type", ""))
    if any(ct in content_type for ct in ["image/", "font/", "application/octet-stream", "application/zip"]):
        is_base64 = True
        body_str = base64.b64encode(response_body).decode("utf-8")
    else:
        try:
            body_str = response_body.decode("utf-8")
        except UnicodeDecodeError:
            is_base64 = True
            body_str = base64.b64encode(response_body).decode("utf-8")

    return {
        "statusCode": response_status[0],
        "headers": out_headers,
        "body": body_str,
        "isBase64Encoded": is_base64
    }
