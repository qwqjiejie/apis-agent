from fastapi.responses import JSONResponse


def ok(data=None, message: str = "success"):
    return JSONResponse({"code": 200, "data": data, "message": message})


def ok_paged(records, total, page, size):
    return JSONResponse({
        "code": 200,
        "data": {"records": records, "total": total, "current": page, "size": size},
        "message": "success",
    })


def error(code: int, message: str):
    return JSONResponse({"code": code, "data": None, "message": message})
