from __future__ import annotations

import json
from typing import Any


def sse_step(step: str, status: str, message: str) -> str:
    return f"event: step\ndata: {json.dumps({'step': step, 'status': status, 'message': message})}\n\n"


def sse_complete(data: Any) -> str:
    return f"event: complete\ndata: {json.dumps(data)}\n\n"


def sse_error(message: str) -> str:
    return f"event: error\ndata: {json.dumps({'message': message})}\n\n"
