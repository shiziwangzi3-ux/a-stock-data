# a-stock-data
A股行情双源核准数据
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime.now(TZ)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
}

STOCKS = [
    ("SZ.002460", "赣锋锂业", "0.002460", "sz002460"),
    ("SH.601600", "中国铝业", "1.601600", "sh601600"),
]


def to_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_eastmoney(secid):
    response = requests.get(
        "https://push2.eastmoney.com/api/qt/stock/get",
        params={
            "secid": secid,
            "fltt": 2,
            "invt": 2,
            "fields": (
                "f43,f44,f45,f46,f47,f48,"
                "f57,f58,f60,f86,f169,f170,f171"
            ),
            "_": int(NOW.timestamp() * 1000),
        },
        headers={
            **HEADERS,
            "Referer": "https://quote.eastmoney.com/",
        },
        timeout=15,
    )

    response.raise_for_status()

    data = response.json().get("data")

    if not data:
        raise RuntimeError("东方财富返回空数据")

    timestamp = datetime.fromtimestamp(
        int(data["f86"]),
        TZ,
    ).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "source": "eastmoney",
        "timestamp": timestamp,
        "latest": to_number(data.get("f43")),
        "prevClose": to_number(data.get("f60")),
        "open": to_number(data.get("f46")),
        "high": to_number(data.get("f44")),
        "low": to_number(data.get("f45")),
        "volume": to_number(data.get("f47")),
        "turnover": to_number(data.get("f48")),
        "change": to_number(data.get("f169")),
        "pctChange": to_number(data.get("f170")),
        "amplitude": to_number(data.get("f171")),
    }


def fetch_tencent():
    symbols = ",".join(stock[3] for stock in STOCKS)

    response = requests.get(
        (
            f"https://qt.gtimg.cn/q={symbols}"
            f"&_={int(NOW.timestamp() * 1000)}"
        ),
        headers={
            **HEADERS,
            "Referer": "https://gu.qq.com/",
        },
        timeout=15,
    )

    response.raise_for_status()

    text = response.content.decode(
        "gbk",
        errors="ignore",
    )

    output = {}

    matches = re.findall(
        r'v_((?:sz|sh)\d{6})="([^"]*)"',
        text,
    )

    for symbol, body in matches:
        fields = body.split("~")

        if len(fields) < 39:
            continue

        code = (
            f"{symbol[:2].upper()}."
            f"{symbol[2:]}"
        )

        timestamp = None
        raw_time = fields[30]

        if re.fullmatch(r"\d{14}", raw_time):
            timestamp = (
                datetime.strptime(
                    raw_time,
                    "%Y%m%d%H%M%S",
                )
                .replace(tzinfo=TZ)
                .strftime("%Y-%m-%d %H:%M:%S")
            )

        amount = to_number(fields[37])

        output[code] = {
            "source": "tencent",
            "timestamp": timestamp,
            "latest": to_number(fields[3]),
            "prevClose": to_number(fields[4]),
            "open": to_number(fields[5]),
            "high": to_number(fields[33]),
            "low": to_number(fields[34]),
            "volume": to_number(fields[36]),
            "turnover": (
                amount * 10000
                if amount is not None
                else None
            ),
            "turnoverRate": to_number(fields[38]),
        }

    if not output:
        raise RuntimeError("腾讯行情无法解析")

    return output


def price_difference(value_a, value_b):
    if value_a is None or value_b is None:
        return None

    return round(
        abs(value_a - value_b),
        6,
    )


def verify_quotes(eastmoney, tencent):
    errors = []

    limits = {
        "latest": 0.05,
        "prevClose": 0.02,
        "open": 0.02,
        "high": 0.05,
        "low": 0.05,
    }

    differences = {}

    for field, limit in limits.items():
        differences[field] = price_difference(
            eastmoney.get(field),
            tencent.get(field),
        )

        if (
            differences[field] is None
            or differences[field] > limit
        ):
            errors.append(
                f"{field}_mismatch"
            )

    eastmoney_time = eastmoney.get("timestamp")
    tencent_time = tencent.get("timestamp")

    if not eastmoney_time or not tencent_time:
        errors.append("timestamp_missing")

    elif eastmoney_time[:10] != tencent_time[:10]:
        errors.append("trading_date_mismatch")

    for source_name, quote in (
        ("eastmoney", eastmoney),
        ("tencent", tencent),
    ):
        latest = quote.get("latest")
        open_price = quote.get("open")
        high = quote.get("high")
        low = quote.get("low")

        required = [
            latest,
            open_price,
            high,
            low,
        ]

        if any(value is None for value in required):
            errors.append(
                f"{source_name}_required_field_missing"
            )
            continue

        if high < max(latest, open_price):
            errors.append(
                f"{source_name}_high_logic_error"
            )

        if low > min(latest, open_price):
            errors.append(
                f"{source_name}_low_logic_error"
            )

    return {
        "passed": not errors,
        "errors": errors,
        "differences": differences,
    }


def main():
    result = {
        "generatedAt": NOW.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "timezone": "Asia/Shanghai",
        "status": "行情核准失败",
        "allowTradeAnalysis": False,
        "stocks": {},
        "errors": [],
    }

    try:
        tencent_quotes = fetch_tencent()

    except Exception as error:
        tencent_quotes = {}

        result["errors"].append(
            f"tencent_error: {error}"
        )

    for code, name, secid, _ in STOCKS:
        try:
            eastmoney_quote = fetch_eastmoney(
                secid
            )

        except Exception as error:
            eastmoney_quote = None

            result["errors"].append(
                f"{code}_eastmoney_error: {error}"
            )

        tencent_quote = tencent_quotes.get(code)

        if eastmoney_quote and tencent_quote:
            verification = verify_quotes(
                eastmoney_quote,
                tencent_quote,
            )

        else:
            verification = {
                "passed": False,
                "errors": ["source_missing"],
                "differences": {},
            }

        result["stocks"][code] = {
            "code": code,
            "name": name,
            "eastmoney": eastmoney_quote,
            "tencent": tencent_quote,
            "verification": verification,
        }

    result["allowTradeAnalysis"] = all(
        stock["verification"]["passed"]
        for stock in result["stocks"].values()
    )

    result["status"] = (
        "行情核准成功"
        if result["allowTradeAnalysis"]
        else "行情核准失败"
    )

    Path("latest.json").write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
