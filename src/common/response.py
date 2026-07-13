from fastapi.responses import JSONResponse


def ok(data=None):
    return JSONResponse({"code": 200, "data": data, "message": "success"})


def ok_paged(records, total, page, size):
    return JSONResponse({
        "code": 200,
        "data": {"records": records, "total": total, "current": page, "size": size},
        "message": "success",
    })
